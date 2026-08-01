import pytest
import ebooklib
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


def chapter_bodies(book):
    return [d.get_body_content().decode()
            for d in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
            if not d.file_name.endswith('nav.xhtml')]


def test_two_column_html():
    bb = BookBuilder.__new__(BookBuilder)
    aligned = [{"source_sents": [{"html": "A", "first": False}],
                  "target_sents": [{"html": "B", "first": False}]}]
    html = bb._build_two_column_html(aligned)
    assert '<td class="bilingual-left">A</td>' in html
    assert '<td class="bilingual-right">B</td>' in html
    assert 'class="bilingual-table"' in html


def test_two_column_html_indents_paragraph_starts():
    bb = BookBuilder.__new__(BookBuilder)
    aligned = [{"source_sents": [{"html": "<p>A</p>", "first": True},
                                  {"html": "<p>B</p>", "first": False}],
                  "target_sents": [{"html": "<p>C</p>", "first": False}]}]
    html = bb._build_two_column_html(aligned)
    # Only the paragraph-start sentence gets the indent style
    assert html.count('text-indent: 2em !important') == 1
    assert 'class="bilingual-table"' in html


def test_build_bilingual_chapter(fs):
    src_file, tgt_file = build_epub_files(fs)
    block = {
        "source": {"toc_path": ["Source Ch"], "content_html": "", "index": 0,
                   "footnote_placeholders": []},
        "target": {"toc_path": ["Target Ch"], "content_html": "", "index": 0,
                   "footnote_placeholders": []},
        "alignment": [{"source_sents": [{"html": "<p>Hello</p>", "first": True}],
                         "target_sents": [{"html": "<p>Bonjour</p>", "first": True}]}]
    }
    bb = BookBuilder(source_book=src_file, target_book=tgt_file, blocks=[block])
    new_book = bb.run()
    assert new_book is not None
    body = '\n'.join(chapter_bodies(new_book))
    assert "bilingual-left" in body
    assert "bilingual-right" in body
    assert "Hello" in body
    assert "Bonjour" in body


def test_single_side_chapter(fs):
    src_file, tgt_file = build_epub_files(fs)
    block = {
        "source": {"toc_path": ["Only Src"], "content_html": "<p>Some text</p>",
                   "index": 0, "footnote_placeholders": []},
        "target": None,
        "alignment": []
    }
    bb = BookBuilder(source_book=src_file, target_book=tgt_file, blocks=[block])
    new_book = bb.run()
    body = '\n'.join(chapter_bodies(new_book))
    assert "Some text" in body
    assert "bilingual-table" not in body


def test_toc_hierarchy_preserved():
    bb = BookBuilder.__new__(BookBuilder)
    toc = bb._build_toc([
        {'file_name': 'chap_000.xhtml', 'uid': 'chap_000',
         'toc_path': ['Part I', 'Chapter 1']},
        {'file_name': 'chap_001.xhtml', 'uid': 'chap_001',
         'toc_path': ['Part I', 'Chapter 2']},
    ])
    assert len(toc) == 1
    section, children = toc[0]
    assert section.title == 'Part I'
    assert len(children) == 2
    assert children[0].title == 'Chapter 1'
    assert children[1].title == 'Chapter 2'


def test_footnotes_in_output(fs):
    src_file, tgt_file = build_epub_files(fs)
    block = {
        "source": {"toc_path": ["S"], "content_html": "", "index": 0,
                   "footnote_placeholders": [{"token": "S_FNREF_1", "target_id": "fn1"}]},
        "target": {"toc_path": ["T"], "content_html": "", "index": 0,
                   "footnote_placeholders": [{"token": "T_FNREF_2", "target_id": "fn2"}]},
        "alignment": [{"source_sents": [{"html": "<p>Text S_FNREF_1</p>", "first": False}],
                         "target_sents": [{"html": "<p>Text T_FNREF_2</p>", "first": False}]}]
    }
    bb = BookBuilder(source_book=src_file, target_book=tgt_file, blocks=[block],
                     source_footnotes={"fn1": "Source footnote"},
                     target_footnotes={"fn2": "Target footnote"})
    new_book = bb.run()
    body = '\n'.join(chapter_bodies(new_book))
    assert "Source footnote" in body
    assert "Target footnote" in body
    assert 'class="footnotes"' in body


def test_styles_not_copied(fs):
    """The built book links only its own generic.css, never the source/target styles."""
    src_bytes = make_epub_bytes([], title="Src", styles=[("src_style.css", b"body{color:red;}")])
    tgt_bytes = make_epub_bytes([{"filename": "ch.xhtml", "content": "<p>t</p>"}],
                                title="Tgt", styles=[("tgt_style.css", b"body{color:blue;}")])
    src_path = write_epub_to_fake(fs, src_bytes, "src.epub")
    tgt_path = write_epub_to_fake(fs, tgt_bytes, "tgt.epub")
    src_file = EpubFile(src_path)
    tgt_file = EpubFile(tgt_path)

    bb = BookBuilder(source_book=src_file, target_book=tgt_file, blocks=[])
    new_book = bb.run()
    assert new_book is not None
    all_files = [i.file_name for i in new_book.get_items()]
    assert "generic.css" in all_files
    assert "src_style.css" not in all_files
    assert "tgt_style.css" not in all_files


def test_relative_href_nested(fs):
    tgt_bytes = make_epub_bytes(
        [{"filename": "text/ch.xhtml", "content": create_chapter_html("T Ch1", "Tgt text")}],
        title="Tgt")
    tgt_path = write_epub_to_fake(fs, tgt_bytes, "tgt.epub")
    bb = BookBuilder.__new__(BookBuilder)
    bb.target_book = EpubFile(tgt_path).ebook
    assert bb._relative_href('Styles/style.css') == '../Styles/style.css'
    assert bb._relative_href('text/style.css') == 'style.css'


def test_indent_wraps_inline_fragment():
    bb = BookBuilder.__new__(BookBuilder)
    wrapped = bb._apply_indent_to_block('<i>Italic sentence.</i>')
    assert wrapped.startswith('<span')
    assert 'display: block' in wrapped
    assert 'text-indent: 2em' in wrapped
    block = bb._apply_indent_to_block('<p>Sentence.</p>')
    assert '<p' in block
    assert 'text-indent: 2em' in block


def test_epub_file_rejects_garbage(fs):
    fs.create_file("/fake/bad.epub", contents=b"this is not an epub")
    assert not EpubFile("/fake/bad.epub")
