# tests/conftest.py
import io
import pytest
from unittest.mock import patch, MagicMock
from ebooklib import epub
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
# Helpers to build in‑memory EPUBs
# ----------------------------------------------------------------------
def create_chapter_html(title, body, footnotes=None, extra_headings=None):
    """Return an XHTML string with optional heading, paragraphs, footnotes,
    and extra sub-headings if needed."""
    h = f"<h1>{title}</h1>" if title else ""
    extra = "\n".join(f"<h{level}>{text}</h{level}>"
                      for level, text in (extra_headings or [])) if extra_headings else ""
    p = "\n".join(f"<p>{line}</p>" for line in body.split("\n")) if body else ""
    fn_html = ""
    if footnotes:
        fn_html = "<hr/>"
        for i, (ref, fn_body) in enumerate(footnotes, 1):
            fn_html += f'<p id="fn{i}"><a href="#fnref{i}">[{i}]</a> {fn_body}</p>'
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{title or ''}</title></head>
<body>
{h}
{extra}
{p}
{fn_html}
</body>
</html>"""

def make_epub_bytes(chapters, title="Test Book", lang="en",
                    toc_entries=None, guide=None, styles=None, cover=False):
    """
    Build a minimal EPUB in memory and return its bytes.

    chapters: list of dicts with keys:
        - filename (str)
        - content (str, HTML)
        - linear (bool, default True)
        - id (optional)
    toc_entries: list of (label, href) for the NCX TOC. If None, no TOC.
    guide: list of (type, href) for the guide section.
    styles: list of (filename, css_bytes) tuples.
    cover: if True, a dummy cover image is added.
    """
    book = epub.EpubBook()
    book.set_title(title)
    book.set_language(lang)
    book.add_author("Test Author")

    if cover:
        book.set_cover("cover.jpg", b"fake-image-data")

    if styles:
        for fname, css in styles:
            item = epub.EpubItem(uid=fname, file_name=fname,
                                 media_type="text/css", content=css)
            book.add_item(item)

    spine = []
    manifest = {}
    for ch in chapters:
        uid = ch.get("id", ch["filename"])
        item = epub.EpubHtml(uid=uid, file_name=ch["filename"],
                             media_type="application/xhtml+xml")
        item.set_content(ch["content"].encode("utf-8"))
        book.add_item(item)
        manifest[uid] = item
        if ch.get("linear", True):
            spine.append(item)

    book.spine = spine

    if toc_entries:
        toc = []
        for label, href in toc_entries:
            uid = href.split("#")[0] if "#" in href else href
            if uid in manifest:
                toc.append(epub.Link(href, label, uid))
            else:
                toc.append(epub.Link(href, label, uid))
        book.toc = toc

    if guide:
        book.guide = [{'type': t, 'href': h} for t, h in guide]

    buf = io.BytesIO()
    epub.write_epub(buf, book)
    return buf.getvalue()


class MockDocument:
    """Mimics the epub_utils.Document interface from bytes."""
    def __init__(self, path_or_bytes):
        if isinstance(path_or_bytes, bytes):
            self._book = epub.read_epub(io.BytesIO(path_or_bytes))
        else:
            raise ValueError("Only bytes accepted in tests")
        self.container = MagicMock()
        self.container.rootfile_path = "package.opf"
        self.package = self._book
        self.toc = self._book

    def get_file_by_path(self, href):
        item = self._book.get_item_with_href(href)
        class FakeFile:
            def to_str(self):
                return item.get_content().decode("utf-8")
        if item:
            return FakeFile()
        raise KeyError(href)


@pytest.fixture
def patch_document():
    with patch("bbb.extractor.Document", MockDocument):
        yield


@pytest.fixture
def patch_read_epub():
    original = epub.read_epub
    def fake_read(path):
        if isinstance(path, bytes):
            return epub.read_epub(io.BytesIO(path))
        return original(path)
    with patch("bbb.book_builder.epub.read_epub", fake_read):
        yield