import io, pytest
from ebooklib import epub
from unittest.mock import patch
from bbb.book_builder import BookBuilder
from conftest import make_epub_bytes, create_chapter_html

def build_epubs(src_chapters=None, tgt_chapters=None, src_title="Src", tgt_title="Tgt"):
    src = make_epub_bytes(src_chapters or [
        {"filename": "ch1.xhtml", "content": create_chapter_html("S Ch1", "Src text")}
    ], title=src_title)
    tgt = make_epub_bytes(tgt_chapters or [
        {"filename": "ch1.xhtml", "content": create_chapter_html("T Ch1", "Tgt text")}
    ], title=tgt_title)
    return src, tgt

def test_two_column_html():
    bb = BookBuilder.__new__(BookBuilder)
    aligned = [[{"source": "A", "target": "B"}]]
    html = bb._build_two_column_html(aligned)
    assert '<td class="bilingual-left">A</td>' in html
    assert '<td class="bilingual-right">B</td>' in html
    assert 'class="bilingual-table"' in html

def test_bilingual_layout_with_header():
    src_bytes, tgt_bytes = build_epubs()
    bb = BookBuilder(src_bytes, tgt_bytes, [])
    block = {
        "source": {"display_path": ["Source Ch"], "toc_path": ["Source Ch"]},
        "target": {"display_path": ["Target Ch"], "toc_path": ["Target Ch"]},
        "alignment": [[{"source": "Hello", "target": "Bonjour",
                        "source_footnote_occurrences": [],
                        "target_footnote_occurrences": []}]]
    }
    ch = bb._build_chapter(block["source"], block["target"],
                            block["alignment"], [], [])
    assert "bilingual-left" in ch["body_html"]
    assert "bilingual-right" in ch["body_html"]
    assert "Hello" in ch["body_html"]
    assert "Bonjour" in ch["body_html"]
    assert "Source Ch" in ch["body_html"]
    assert "Target Ch" in ch["body_html"]

def test_single_side_chapter():
    src_bytes, tgt_bytes = build_epubs()
    bb = BookBuilder(src_bytes, tgt_bytes, [])
    result = bb._build_single_side_chapter(
        {"display_path": ["Only Src"], "toc_path": ["Only Src"], "text": "Some text"},
        {}, [], "bilingual-source-only"
    )
    assert "bilingual-source-only" in result["body_html"]
    assert "Some text" in result["body_html"]
    assert "bilingual-table" not in result["body_html"]

def test_chapter_hierarchy_preserved():
    src_bytes, tgt_bytes = build_epubs()
    bb = BookBuilder(src_bytes, tgt_bytes, [])
    src_info = {"display_path": ["Part I", "Chapter 1"], "toc_path": ["Part I", "Chapter 1"]}
    tgt_info = {"display_path": ["Teil I", "Kapitel 1"], "toc_path": ["Teil I", "Kapitel 1"]}
    ch = bb._build_chapter(src_info, tgt_info, [], [], [])
    assert "<h1" in ch["body_html"]
    assert "<h2" in ch["body_html"]
    assert "Part I</h1>" in ch["body_html"]
    assert "Chapter 1</h2>" in ch["body_html"]

def test_footnotes_in_output():
    src_bytes, tgt_bytes = build_epubs()
    bb = BookBuilder(src_bytes, tgt_bytes, [],
                     source_footnotes={"fn1": "Source footnote"},
                     target_footnotes={"fn2": "Target footnote"})
    block = {
        "source": {"display_path": ["S"], "toc_path": ["S"]},
        "target": {"display_path": ["T"], "toc_path": ["T"]},
        "alignment": [[{"source": "Text S_FNREF_1", "target": "Text T_FNREF_2",
                        "source_footnote_occurrences": [{"token": "S_FNREF_1", "target_id": "fn1"}],
                        "target_footnote_occurrences": [{"token": "T_FNREF_2", "target_id": "fn2"}]}]]
    }
    ch = bb._build_chapter(block["source"], block["target"],
                            block["alignment"], [], [])
    assert "Source footnote" in ch["body_html"]
    assert "Target footnote" in ch["body_html"]
    assert "footnotes" in ch["body_html"]

def test_css_copied():
    """Stylesheet from target epub is copied into new book."""
    src_bytes = make_epub_bytes([], title="Src")
    tgt_bytes = make_epub_bytes([{"filename": "ch.xhtml", "content": "<p>t</p>"}],
                                title="Tgt", styles=[("style.css", b"body{color:red;}")])

    src_book = epub.read_epub(io.BytesIO(src_bytes))
    tgt_book = epub.read_epub(io.BytesIO(tgt_bytes))

    with patch('bbb.book_builder.epub.read_epub') as mock_read:
        mock_read.side_effect = lambda path: src_book if path is src_bytes else tgt_book
        bb = BookBuilder(src_bytes, tgt_bytes, [])
        new_book = bb.run()
        assert new_book is not None

        # Check that the stylesheet file_name appears among the output items
        all_files = [i.file_name for i in new_book.get_items()]
        assert "style.css" in all_files