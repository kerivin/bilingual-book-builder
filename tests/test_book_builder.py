import pytest
from ebooklib import epub
from bbb.epub_file import EpubFile
from bbb.book_builder import BookBuilder
from conftest import make_epub_bytes, create_chapter_html, write_epub_to_fake


def build_epub_files(fs):
    """Create source and target EpubFile objects on fake fs."""
    src_bytes = make_epub_bytes(
        [{"filename": "ch1.xhtml", "content": create_chapter_html("S Ch1", "Src text")}],
        title="Src"
    )
    tgt_bytes = make_epub_bytes(
        [{"filename": "ch1.xhtml", "content": create_chapter_html("T Ch1", "Tgt text")}],
        title="Tgt"
    )
    src_path = write_epub_to_fake(fs, src_bytes, "src.epub")
    tgt_path = write_epub_to_fake(fs, tgt_bytes, "tgt.epub")
    return EpubFile(src_path), EpubFile(tgt_path)


def test_two_column_html():
    bb = BookBuilder.__new__(BookBuilder)
    aligned = [[{"source": "A", "target": "B"}]]
    html = bb._build_two_column_html(aligned)
    assert '<td class="bilingual-left">A</td>' in html
    assert '<td class="bilingual-right">B</td>' in html
    assert 'class="bilingual-table"' in html


def test_bilingual_layout_with_header(fs):
    src_file, tgt_file = build_epub_files(fs)
    bb = BookBuilder(source_book=src_file, target_book=tgt_file, blocks=[])
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


def test_single_side_chapter(fs):
    src_file, tgt_file = build_epub_files(fs)
    bb = BookBuilder(source_book=src_file, target_book=tgt_file, blocks=[])
    result = bb._build_single_side_chapter(
        {"display_path": ["Only Src"], "toc_path": ["Only Src"], "text": "Some text"},
        {}, [], "bilingual-source-only"
    )
    assert "bilingual-source-only" in result["body_html"]
    assert "Some text" in result["body_html"]
    assert "bilingual-table" not in result["body_html"]


def test_chapter_hierarchy_preserved(fs):
    src_file, tgt_file = build_epub_files(fs)
    bb = BookBuilder(source_book=src_file, target_book=tgt_file, blocks=[])
    src_info = {"display_path": ["Part I", "Chapter 1"], "toc_path": ["Part I", "Chapter 1"]}
    tgt_info = {"display_path": ["Teil I", "Kapitel 1"], "toc_path": ["Teil I", "Kapitel 1"]}
    ch = bb._build_chapter(src_info, tgt_info, [], [], [])
    assert "<h1" in ch["body_html"]
    assert "<h2" in ch["body_html"]
    assert "Part I</h1>" in ch["body_html"]
    assert "Chapter 1</h2>" in ch["body_html"]


def test_footnotes_in_output(fs):
    src_file, tgt_file = build_epub_files(fs)
    bb = BookBuilder(source_book=src_file, target_book=tgt_file, blocks=[],
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


def test_css_copied(fs):
    """Stylesheet from target epub is copied into new book."""
    src_bytes = make_epub_bytes([], title="Src")
    tgt_bytes = make_epub_bytes([{"filename": "ch.xhtml", "content": "<p>t</p>"}],
                                title="Tgt", styles=[("style.css", b"body{color:red;}")])
    src_path = write_epub_to_fake(fs, src_bytes, "src.epub")
    tgt_path = write_epub_to_fake(fs, tgt_bytes, "tgt.epub")
    src_file = EpubFile(src_path)
    tgt_file = EpubFile(tgt_path)

    bb = BookBuilder(source_book=src_file, target_book=tgt_file, blocks=[])
    new_book = bb.run()
    assert new_book is not None
    all_files = [i.file_name for i in new_book.get_items()]
    assert "style.css" in all_files