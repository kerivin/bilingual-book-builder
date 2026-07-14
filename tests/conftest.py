import io
import pytest
from unittest.mock import patch, MagicMock
from pyfakefs.fake_filesystem_unittest import Patcher
from ebooklib import epub

_original_read_epub = epub.read_epub

def create_chapter_html(title, body, footnotes=None, extra_headings=None):
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
    book = epub.EpubBook()
    book.set_title(title)
    book.set_language(lang)
    book.add_author("Test Author")
    if cover:
        book.set_cover("cover.jpg", b"fake-image-data")
    if styles:
        for fname, css in styles:
            book.add_item(epub.EpubItem(uid=fname, file_name=fname,
                                        media_type="text/css", content=css))
    spine_ids = []
    for ch in chapters:
        uid = ch.get("id", ch["filename"])
        item = epub.EpubHtml(uid=uid, file_name=ch["filename"],
                             media_type="application/xhtml+xml")
        item.set_content(ch["content"].encode("utf-8"))
        book.add_item(item)
        if ch.get("linear", True):
            spine_ids.append(uid)
    book.spine = spine_ids
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    if toc_entries:
        toc = []
        for label, href in toc_entries:
            uid = href.split("#")[0] if "#" in href else href
            toc.append(epub.Link(href, label, uid))
        book.toc = toc
    if guide:
        book.guide = [{'type': t, 'href': h} for t, h in guide]
    buf = io.BytesIO()
    epub.write_epub(buf, book)
    return buf.getvalue()


class MockTocItem:
    def __init__(self, label, target, children=None):
        self.label = label
        self.target = target
        self.children = children or []

class MockToc:
    def __init__(self, items):
        self._items = items
    def get_toc_items(self):
        return self._items

def _convert_epub_toc(toc_list):
    result = []
    for item in toc_list:
        if isinstance(item, epub.Link):
            result.append(MockTocItem(item.title, item.href))
        elif isinstance(item, tuple):
            section, children = item
            if isinstance(section, epub.Section):
                child_items = _convert_epub_toc(children)
                result.append(MockTocItem(section.title, section.href, child_items))
    return result


class MockDocument:
    def __init__(self, path_or_bytes):
        if isinstance(path_or_bytes, bytes):
            self._book = _original_read_epub(io.BytesIO(path_or_bytes))
        else:
            raise ValueError("Only bytes accepted in tests")

        self.container = MagicMock()
        self.container.rootfile_path = "package.opf"

        pkg = MagicMock()
        md = MagicMock()
        titles = self._book.get_metadata('DC', 'title')
        md.title = titles[0][0] if titles else "Untitled"
        pkg.metadata = md

        manifest_items = [{'id': it.get_id(), 'href': it.file_name} for it in self._book.get_items()]
        manifest = MagicMock()
        manifest.items = manifest_items
        pkg.manifest = manifest

        # The spine after reading is a list of (idref, linear) tuples
        spine_ids = getattr(self._book, 'spine', []) or []
        spine_itemrefs = []
        for entry in spine_ids:
            if isinstance(entry, tuple):
                idref = entry[0]
            else:
                idref = entry
            spine_itemrefs.append({'idref': idref, 'linear': 'yes'})
        spine = MagicMock()
        spine.itemrefs = spine_itemrefs
        pkg.spine = spine

        if hasattr(self._book, 'guide') and self._book.guide:
            pkg.guide = self._book.guide
        else:
            pkg.guide = []

        self.package = pkg

        if hasattr(self._book, 'toc') and self._book.toc:
            toc_items = _convert_epub_toc(self._book.toc)
            self.toc = MockToc(toc_items)
        else:
            self.toc = None

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
    with patch("bbb.book_builder.epub.read_epub", side_effect=_original_read_epub):
        yield

@pytest.fixture
def fs():
    with Patcher() as patcher:
        yield patcher.fs