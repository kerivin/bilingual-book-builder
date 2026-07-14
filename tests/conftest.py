import io
import pytest
from ebooklib import epub
from pyfakefs.fake_filesystem_unittest import Patcher


def create_chapter_html(title, body, footnotes=None, extra_headings=None):
    h = f"<h1>{title}</h1>" if title else ""
    extra = "\n".join(
        f"<h{level}>{text}</h{level}"
        for level, text in (extra_headings or [])
    ) if extra_headings else ""
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


@pytest.fixture
def fs():
    with Patcher() as patcher:
        yield patcher.fs


def write_epub_to_fake(fs, epub_bytes, name="test.epub"):
    """Write epub bytes to a virtual file and return its path."""
    if not fs.exists("/fake"):
        fs.create_dir("/fake")
    path = f"/fake/{name}"
    fs.create_file(path, contents=epub_bytes)
    return path