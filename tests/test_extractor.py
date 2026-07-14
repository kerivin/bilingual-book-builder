import pytest
from bbb.extractor import Extractor
from bbb.constants import SRC_FN_PREFIX
from conftest import make_epub_bytes, create_chapter_html, write_epub_to_fake

def test_single_chapter(fs):
    ch = {"filename": "ch1.xhtml", "content": create_chapter_html("Intro", "Hello world.")}
    epub = make_epub_bytes([ch])
    path = write_epub_to_fake(fs, epub)
    chapters, _ = Extractor(path, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) == 1
    assert chapters[0]["display_path"][0] == "Intro"

def test_multiple_chapters_with_toc(fs):
    ch1 = {"filename": "c1.xhtml", "content": create_chapter_html("One", "Content one")}
    ch2 = {"filename": "c2.xhtml", "content": create_chapter_html("Two", "Content two")}
    epub = make_epub_bytes([ch1, ch2],
                           toc_entries=[("Ch One", "c1.xhtml"), ("Ch Two", "c2.xhtml")])
    path = write_epub_to_fake(fs, epub)
    chapters, _ = Extractor(path, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) == 2
    assert chapters[0]["display_path"][-1] == "Ch One"

def test_nested_toc(fs):
    html1 = create_chapter_html("Part I", "Intro")
    html2 = create_chapter_html("Chapter 1", "Text")
    html3 = create_chapter_html("Chapter 2", "Text")
    epub = make_epub_bytes(
        [{"filename": "p1.xhtml", "content": html1},
         {"filename": "c1.xhtml", "content": html2},
         {"filename": "c2.xhtml", "content": html3}],
        toc_entries=[
            ("Part I", "p1.xhtml"),
            ("Chap 1", "c1.xhtml"),
            ("Chap 2", "c2.xhtml")
        ]
    )
    path = write_epub_to_fake(fs, epub)
    chapters, _ = Extractor(path, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) == 3
    assert chapters[0]["display_path"][0] == "Part I"

def test_header_fallback(fs):
    ch1 = {"filename": "a.xhtml", "content": create_chapter_html("H1", "Text")}
    ch2 = {"filename": "b.xhtml", "content": create_chapter_html("H2", "Text")}
    epub = make_epub_bytes([ch1, ch2])  # no TOC
    path = write_epub_to_fake(fs, epub)
    chapters, _ = Extractor(path, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) >= 2
    titles = [c["display_path"][0] for c in chapters]
    assert "H1" in titles and "H2" in titles

def test_guide_skip(fs):
    cover_html = create_chapter_html("Cover", "Cover image")
    content_html = create_chapter_html("Chapter 1", "The story begins")
    epub = make_epub_bytes(
        [{"filename": "cover.xhtml", "content": cover_html},
         {"filename": "ch1.xhtml", "content": content_html}],
        toc_entries=[("Cover", "cover.xhtml"), ("Ch1", "ch1.xhtml")],
        guide=[("cover", "cover.xhtml")]
    )
    path = write_epub_to_fake(fs, epub)
    chapters, _ = Extractor(path, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) == 1
    assert chapters[0]["display_path"][-1] == "Ch1"

def test_footnotes_removed(fs):
    html = """<html><body>
    <h1>Ch</h1>
    <p>Text<a href="#fn1" id="fnref1"><sup>1</sup></a> end.</p>
    <p id="fn1">Footnote content.</p>
    </body></html>"""
    epub = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub)
    chapters, fn_map = Extractor(path, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(fn_map) == 1
    assert "Footnote content" in list(fn_map.values())[0]
    text = chapters[0]["full_text"]
    assert "S_FNREF_1" in text
    assert "Footnote content" not in text

def test_paragraphs_preserved(fs):
    html = create_chapter_html("Title", "Paragraph one.\n\nParagraph two.")
    epub = make_epub_bytes([{"filename": "p.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub)
    chapters, _ = Extractor(path, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    text = chapters[0]["full_text"]
    assert "Paragraph one." in text
    assert "Paragraph two." in text
    assert text.count("\n\n") == 1

def test_heading_with_styles(fs):
    html = """<html><body>
    <h1 class="chapter">Chapter I</h1>
    <p>Body</p>
    <h2 class="sub">Section A</h2>
    <p>More</p>
    </body></html>"""
    epub = make_epub_bytes([{"filename": "s.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub)
    chapters, _ = Extractor(path, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) >= 1
    assert "Chapter I" in chapters[0]["display_path"][0]

def test_empty_book(fs):
    epub = make_epub_bytes([])
    path = write_epub_to_fake(fs, epub)
    chapters, _ = Extractor(path, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert chapters == []