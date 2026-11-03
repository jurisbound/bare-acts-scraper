"""A light reading copy of a very large Act PDF.

Some PDFs on India Code are 70-300 MB, not because the Act is long but because of how they were made:

  * a scan in colour at photo resolution (the Assam Panchayat Act, 1994: 95 pages, 72 MB): every page
    picture is saved again in black and white at reading resolution (about 200 dpi). The pages keep
    their text layer, so search and section cutting read exactly the same text;
  * pages whose letters were turned into drawn shapes, with no text layer and no pictures (a Daman
    Gazette issue, 55 pages, 76 MB): every page becomes one black-and-white picture of the page.

Measured on those two: 72 MB -> 10 MB and 76 MB -> 2.8 MB, both still clearly readable. Only that
copy is kept. A PDF that is large for other reasons (real text, fonts) is left as it is.
"""
from __future__ import annotations

import io
import zlib
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image, ImageOps

SHRINK_ABOVE = 20 * 1024 * 1024        # PDFs bigger than this get a reading copy
_LONGEST_SIDE = 2339                   # pixels on the long side: about 200 dpi on an A4 page
_DPI = 200
_INK = 160                             # grey level below which a pixel counts as ink


def _black_and_white(im: Image.Image) -> Image.Image:
    if max(im.size) > _LONGEST_SIDE:
        f = _LONGEST_SIDE / max(im.size)
        im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
    im = ImageOps.autocontrast(im.convert("L"), cutoff=1)
    return im.point(lambda p: 255 if p > _INK else 0).convert("1")


def _scan_pictures(doc: fitz.Document) -> None:
    """Every page picture replaced in place (the same object: the page's text layer is untouched)."""
    seen: set[int] = set()
    for page in doc:
        for img in page.get_images(full=True):
            xref, smask = img[0], img[1]
            if xref in seen or smask:
                continue                       # a picture with a transparency mask is left alone
            seen.add(xref)
            # decoded by PyMuPDF, which reads every PDF picture format (JPEG, JPEG 2000, JBIG2, fax, …)
            pix = fitz.Pixmap(doc, xref)
            if pix.n - pix.alpha != 1:
                pix = fitz.Pixmap(fitz.csGRAY, pix)
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)
            bw = _black_and_white(Image.frombytes("L", (pix.width, pix.height), pix.samples))
            doc.update_stream(xref, zlib.compress(bw.tobytes(), 9), compress=False)
            for key, value in (("Filter", "/FlateDecode"), ("ColorSpace", "/DeviceGray"), ("BitsPerComponent", "1"),
                               ("Width", str(bw.width)), ("Height", str(bw.height)), ("DecodeParms", "null")):
                doc.xref_set_key(xref, key, value)


def _page_pictures(doc: fitz.Document) -> fitz.Document:
    """Every page as one black-and-white picture (pages made of drawn letters, no text layer)."""
    out = fitz.open()
    for page in doc:
        pix = page.get_pixmap(dpi=_DPI, colorspace=fitz.csGRAY)
        bw = Image.frombytes("L", (pix.width, pix.height), pix.samples).point(lambda p: 255 if p > _INK else 0).convert("1")
        buf = io.BytesIO()
        bw.save(buf, "PNG", optimize=True)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=buf.getvalue())
    return out


def reading_copy(data: bytes) -> tuple[bytes, Optional[str]]:
    """(the PDF to keep, a note when it was made lighter). Small PDFs and PDFs that would not get
    smaller are returned as they are."""
    if len(data) <= SHRINK_ABOVE:
        return data, None
    try:
        return _reading_copy(data)
    except Exception as e:                     # never lose an Act over this: the original is kept
        return data, f"reading copy not made ({type(e).__name__}: {str(e)[:120]}); original kept"


def _reading_copy(data: bytes) -> tuple[bytes, Optional[str]]:
    doc = fitz.open(stream=data, filetype="pdf")
    pages = len(doc) or 1
    with_pictures = sum(1 for p in doc if p.get_images())
    with_text = sum(1 for p in doc if len(p.get_text().strip()) > 200)
    if with_pictures >= pages * 0.5:
        _scan_pictures(doc)
        how = "scanned pages saved in black and white at reading quality; text layer unchanged"
    elif with_text < pages * 0.2:
        doc = _page_pictures(doc)
        how = "pages (letters drawn as shapes, no text layer) saved as black-and-white pictures"
    else:
        return data, None                      # a real text PDF: nothing to gain
    small = doc.tobytes(garbage=4, deflate=True)
    if len(small) >= len(data) * 0.9:
        return data, None
    return small, f"reading copy: {len(data) / 1e6:.0f} MB -> {len(small) / 1e6:.1f} MB ({how})"
