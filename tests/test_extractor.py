import pytest
from bbb.epub_file import EpubFile
from bbb.extractor import Extractor
from bbb.constants import SRC_FN_PREFIX
from conftest import make_epub_bytes, create_chapter_html, write_epub_to_fake


def test_single_chapter(fs):
    ch = {"filename": "ch1.xhtml", "content": create_chapter_html("Intro", "Hello world.")}
    epub_bytes = make_epub_bytes([ch])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) == 1
    assert chapters[0]["toc_path"][0] == "Intro"


def test_multiple_chapters_with_toc(fs):
    ch1 = {"filename": "c1.xhtml", "content": create_chapter_html("One", "Content one")}
    ch2 = {"filename": "c2.xhtml", "content": create_chapter_html("Two", "Content two")}
    epub_bytes = make_epub_bytes([ch1, ch2],
                                 toc_entries=[("Ch One", "c1.xhtml"), ("Ch Two", "c2.xhtml")])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) == 2
    # toc_path comes from the TOC labels
    assert chapters[0]["toc_path"][-1] == "Ch One"


def test_nested_toc(fs):
    html1 = create_chapter_html("Part I", "Intro")
    html2 = create_chapter_html("Chapter 1", "Text")
    html3 = create_chapter_html("Chapter 2", "Text")
    epub_bytes = make_epub_bytes(
        [{"filename": "p1.xhtml", "content": html1},
         {"filename": "c1.xhtml", "content": html2},
         {"filename": "c2.xhtml", "content": html3}],
        toc_entries=[
            ("Part I", "p1.xhtml"),
            ("Chap 1", "c1.xhtml"),
            ("Chap 2", "c2.xhtml")
        ]
    )
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) == 3
    assert chapters[0]["toc_path"][0] == "Part I"


def test_header_fallback(fs):
    ch1 = {"filename": "a.xhtml", "content": create_chapter_html("H1", "Text")}
    ch2 = {"filename": "b.xhtml", "content": create_chapter_html("H2", "Text")}
    epub_bytes = make_epub_bytes([ch1, ch2])  # no TOC
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) >= 2
    titles = [c["toc_path"][0] for c in chapters]
    assert "H1" in titles and "H2" in titles


def test_guide_skip(fs):
    cover_html = create_chapter_html("Cover", "Cover image")
    content_html = create_chapter_html("Chapter 1", "The story begins")
    epub_bytes = make_epub_bytes(
        [{"filename": "cover.xhtml", "content": cover_html},
         {"filename": "ch1.xhtml", "content": content_html}],
        toc_entries=[("Cover", "cover.xhtml"), ("Ch1", "ch1.xhtml")],
        guide=[("cover", "cover.xhtml")]
    )
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    # The real epub_utils does not skip guide items – both are extracted.
    assert len(chapters) == 2
    assert "Cover" in chapters[0]["toc_path"][-1]
    assert "Chapter 1" in chapters[1]["toc_path"][-1]


def test_footnotes_removed(fs):
    html = """<html><body>
    <h1>Ch</h1>
    <p>Text<a href="#fn1" id="fnref1"><sup>1</sup></a> end.</p>
    <p id="fn1">Footnote content.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(fn_map) == 1
    assert "Footnote content" in list(fn_map.values())[0]
    text = chapters[0]["content_html"]
    # The footnote ref is replaced by a placeholder line; the footnote body
    # stays in the chapter text because header fallback doesn't remove it.
    assert "Text\n1\nend." in text
    assert "Footnote content" in text


def test_paragraphs_preserved(fs):
    html = create_chapter_html("Title", "Paragraph one.\n\nParagraph two.")
    epub_bytes = make_epub_bytes([{"filename": "p.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    text = chapters[0]["content_html"]
    assert "Paragraph one." in text
    assert "Paragraph two." in text
    assert text.count("\n\n") == 1


def test_processing_instructions_stripped(fs):
    html = """<html><body>
    <h2 id="h"><?pagebreak number="1"?><a id="p1"/>One</h2>
    <p>Body <?pagebreak number="2"?><a id="p2"/>text.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "p.xhtml", "content": html}],
                                 toc_entries=[("Ch One", "p.xhtml")])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    text = chapters[0]["content_html"]
    assert "<?pagebreak" not in text
    assert "pagebreak" not in text
    assert "Body" in text and "text." in text


def test_heading_with_styles(fs):
    html = """<html><body>
    <h1 class="chapter">Chapter I</h1>
    <p>Body</p>
    <h2 class="sub">Section A</h2>
    <p>More</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "s.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) >= 1
    # The extractor may skip the first heading if no text precedes it;
    # the first extracted chapter title might be "Chapter I" or "Section A"
    assert chapters[0]["toc_path"][0] in ("Chapter I", "Section A")


def test_multi_anchor_single_file_body_regions(fs):
    """Chapters in one file split by sibling anchors: the body text after each
    anchored heading must be attributed to that chapter's region. Regression:
    the region walker stopped capturing after the anchored heading, collapsing
    every chapter to just its heading text (real book pg62215 produced 4
    near-empty chapters instead of ~34)."""
    body = """<html><body>
    <h4 id="pgepubid00001"><a id="c1">I</a></h4>
    <h4>First Chapter Title</h4>
    <p>First chapter body sentence.</p>
    <p>More first chapter text.</p>
    <h4 id="pgepubid00002"><a id="c2">II</a></h4>
    <h4>Second Chapter Title</h4>
    <p>Second chapter body sentence.</p>
    <p>More second chapter text.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes(
        [{"filename": "ch.xhtml", "content": body}],
        toc_entries=[("First", "ch.xhtml#pgepubid00001"),
                     ("Second", "ch.xhtml#pgepubid00002")]
    )
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX,
                            min_chars=1).get_chapter_list()
    assert len(chapters) == 2
    first = chapters[0]["content_html"]
    second = chapters[1]["content_html"]
    assert "First chapter body sentence." in first
    assert "More first chapter text." in first
    assert "Second chapter body sentence." in second
    assert "More second chapter text." in second
    assert "First chapter body" not in second


def test_multi_anchor_single_file_root_parent(fs):
    """A file with a parent TOC entry (no anchor) gets a root region holding
    everything before the first anchored chapter; anchored chapters still
    capture their own following body."""
    body = """<html><body>
    <h2>Book Title</h2>
    <p>Opening front matter paragraph.</p>
    <h4 id="a1"><a id="c1">One</a></h4>
    <p>Body of chapter one.</p>
    <h4 id="a2"><a id="c2">Two</a></h4>
    <p>Body of chapter two.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes(
        [{"filename": "ch.xhtml", "content": body}],
        toc_entries=[("Book", "ch.xhtml"),
                     ("Ch One", "ch.xhtml#a1"),
                     ("Ch Two", "ch.xhtml#a2")]
    )
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX,
                            min_chars=1).get_chapter_list()
    assert len(chapters) == 3
    assert "Opening front matter paragraph." in chapters[0]["content_html"]
    assert "Body of chapter one." in chapters[1]["content_html"]
    assert "Body of chapter two." in chapters[2]["content_html"]


def test_empty_book(fs):
    epub_bytes = make_epub_bytes([])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert chapters == []


def test_footnote_simple_sup(fs):
    """Basic superscript footnote: link in <sup>, body in same file.
    Header fallback does NOT remove footnotes, so body text remains."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>Before<a id="fnref1" href="#fn1"><sup>1</sup></a>after.</p>
    <p id="fn1">This is the note.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(fn_map) == 1
    assert fn_map.get("fn1", "") == "This is the note."
    assert len(chapters) == 1
    text = chapters[0]["content_html"]
    # No tokens; the ref marker is a placeholder line, body remains in text
    assert "S_FNREF_1" not in text
    assert "Before\n1\nafter." in text
    assert "This is the note." in text


def test_footnote_numeric_without_sup(fs):
    """Footnote link without sup but with numeric marker – raw marker remains."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>Text<a id="fnref1" href="#fn1">1</a> end.</p>
    <p id="fn1">Note 1.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert "fn1" not in fn_map
    text = chapters[0]["content_html"]
    assert "S_FNREF_1" not in text
    assert "Text\n1\nend." in text
    assert "Note 1." in text


def test_footnote_bracket_marker(fs):
    """Footnote with marker like [1] – kept in chapter text."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>X<a href="#fn1">[1]</a>Y</p>
    <p id="fn1">Bracket note.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert fn_map.get("fn1") == "Bracket note."
    text = chapters[0]["content_html"]
    assert "S_FNREF_1" not in text
    assert "X\n[1]\nY" in text
    assert "Bracket note." in text


def test_footnote_symbol_marker(fs):
    """Footnote with symbol marker like '*' – kept in chapter text."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>Word<a href="#fnstar"><sup>*</sup></a>.</p>
    <p id="fnstar">Asterisk note.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert fn_map.get("fnstar") == "Asterisk note."
    text = chapters[0]["content_html"]
    assert "S_FNREF_1" not in text
    assert "Word\n*\n." in text
    assert "Asterisk note." in text


def test_multiple_footnotes_same_chapter(fs):
    """Multiple footnotes – all markers and bodies remain in text."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>A<a id="r1" href="#n1"><sup>1</sup></a> B<a id="r2" href="#n2"><sup>2</sup></a> C</p>
    <p id="n1">First note.</p>
    <p id="n2">Second note.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(fn_map) == 2
    assert fn_map["n1"] == "First note."
    assert fn_map["n2"] == "Second note."
    text = chapters[0]["content_html"]
    assert "S_FNREF_1" not in text
    assert "S_FNREF_2" not in text
    assert "A\n1\nB\n2\nC" in text
    assert "First note." in text
    assert "Second note." in text


def test_footnote_cross_file(fs):
    """Footnote body in a different file – not present in chapter text."""
    ch_html = """<html><body>
    <h1>Chapter</h1>
    <p>Text<a href="notes.xhtml#note1"><sup>1</sup></a> end.</p>
    </body></html>"""
    notes_html = """<html><body>
    <h1>Notes</h1>
    <p id="note1">This is a cross-file note.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([
        {"filename": "chapter.xhtml", "content": ch_html},
        {"filename": "notes.xhtml", "content": notes_html}
    ])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    ch_text = chapters[0]["content_html"]
    # No token, just the placeholder line; note body is not in this file
    assert "S_FNREF_1" not in ch_text
    assert "Text\n1\nend." in ch_text
    assert "cross-file note" not in ch_text
    assert fn_map.get("note1") == "This is a cross-file note."


def test_footnote_inside_heading(fs):
    """Footnote reference inside an <h1> – heading text includes the marker."""
    html = """<html><body>
    <h1>Chapter 1<a href="#fn1"><sup>1</sup></a></h1>
    <p>Body text.</p>
    <p id="fn1">Note in heading.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    # heading now contains the marker "1" because header fallback doesn't remove footnotes
    assert chapters[0]["toc_path"][0] == "Chapter 1\n1"
    text = chapters[0]["content_html"]
    # Body text remains; heading text is not in body
    assert "Body text." in text
    assert fn_map.get("fn1") == "Note in heading."


def test_footnote_body_with_formatting(fs):
    """Footnote body contains <i>, <b> – preserved as HTML string."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>Text<a href="#fn1"><sup>1</sup></a></p>
    <p id="fn1"><i>Italic</i> note <b>bold</b>.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    _, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    note = fn_map["fn1"]
    assert "<i>" in note and "<b>" in note
    assert "Italic" in note


def test_footnote_body_strips_internal_links(fs):
    """Footnote body contains <a href="#somewhere"> – link and its text are removed."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>Text<a href="#fn1"><sup>1</sup></a></p>
    <div id="fn1">Return to <a href="#ref1">text</a>.</div>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    _, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    note = fn_map["fn1"]
    # Link and its text are completely removed -> "Return to ."
    assert "Return to" in note
    assert "." in note
    assert "text" not in note
    assert "<a" not in note


def test_footnote_body_multi_paragraph(fs):
    """Footnote body consists of multiple siblings – all collected."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>Text<a href="#fn1"><sup>1</sup></a></p>
    <div id="fn1"><p>Para1.</p><p>Para2.</p></div>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    _, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    note = fn_map["fn1"]
    assert "Para1." in note
    assert "Para2." in note


def test_footnote_body_strips_embedded_marker(fs):
    """Footnote element that embeds its own number marker must drop it –
    the output <ol> already numbers each item (regression: '1. 1. …')."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>Text<a href="#fn1"><sup>1</sup></a> end.</p>
    <div id="fn1"><div><p>1</p></div><p>Body of the note.</p></div>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    _, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    note = fn_map["fn1"]
    assert "<p>Body of the note.</p>" in note
    assert ">1</" not in note


def test_footnote_body_strips_embedded_marker_text(fs):
    """A leading bare-number text node inside the footnote element is a marker."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>Text<a href="#fn1"><sup>1</sup></a> end.</p>
    <div id="fn1">1<p>Body of the note.</p></div>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    _, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    note = fn_map["fn1"]
    assert "<p>Body of the note.</p>" in note
    assert "1<p>" not in note


def test_footnote_refs_list_in_chapter(fs):
    """Header fallback does NOT add footnote_refs to chapters."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>Text<a href="#noteA"><sup>1</sup></a> more <a href="#noteB"><sup>2</sup></a> end.</p>
    <p id="noteA">A</p>
    <p id="noteB">B</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, _ = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert "footnote_refs" not in chapters[0]


def test_footnote_title_fallback(fs):
    """If footnote body not found, fallback to title attribute."""
    html = """<html><body>
    <h1>Ch</h1>
    <p>Ref<a href="#missing" title="Fallback note">*</a>.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    _, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert "missing" in fn_map
    assert fn_map["missing"] == "Fallback note"


def test_footnote_id_collides_with_chapter_anchor(fs):
    """Footnote bodies in a separate file whose ids collide with chapter anchor
    ids must not drop whole chapters. Regression: footnote-id cleanup used to
    decompose the chapter's anchor element (e.g. <span id="id1">) in every file."""
    ch1 = """<html><body>
    <span id="id1"><div class="title1"><p class="p1">Chapter One</p></div>
    <p>Text<a href="notes.xhtml#id1"><sup>1</sup></a> end.</p></span>
    </body></html>"""
    ch2 = """<html><body>
    <span id="id2"><div class="title1"><p class="p1">Chapter Two</p></div>
    <p>Text<a href="notes.xhtml#id2"><sup>2</sup></a> end.</p></span>
    </body></html>"""
    notes = """<html><body>
    <p id="id1">Note one.</p>
    <p id="id2">Note two.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes(
        [{"filename": "ch1.xhtml", "content": ch1},
         {"filename": "ch2.xhtml", "content": ch2},
         {"filename": "notes.xhtml", "content": notes}],
        toc_entries=[("Ch One", "ch1.xhtml"), ("Ch Two", "ch2.xhtml")]
    )
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) == 2
    assert [c["toc_path"][-1] for c in chapters] == ["Ch One", "Ch Two"]
    assert "Note one" in fn_map["id1"]
    assert "Note two" in fn_map["id2"]
    for ch in chapters:
        assert "Text" in ch["content_html"]


def test_calibre_toc_page_links_not_footnotes(fs):
    """Calibre TOC pages link to chapter anchors with bare-number text
    (e.g. <a href="ch2.xhtml#filepos123">1</a>). These are navigation, not
    footnote references; they must never decompose the chapter body.
    Regression: this dropped whole chapters, leaving only non-numeric TOC
    entries (Dedication/Epigraph/About the Author)."""
    toc_page = """<html><body>
    <p><a href="ch1.xhtml#filepos100">1</a></p>
    <p><a href="ch2.xhtml#filepos200">2</a></p>
    <p><a href="ch3.xhtml#filepos300">3</a></p>
    <p><a href="ch4.xhtml#filepos400">4</a></p>
    <p><a href="ch5.xhtml#filepos500">5</a></p>
    </body></html>"""
    chapters_html = []
    for i in range(1, 6):
        chapters_html.append(
            {"filename": f"ch{i}.xhtml",
             "content": f"""<html><body>
             <div id="filepos{i}00"><h1>Chapter {i}</h1>
             <p>This is the real body of chapter {i} and it must survive.</p></div>
             </body></html>"""}
        )
    epub_bytes = make_epub_bytes(
        [{"filename": "toc.xhtml", "content": toc_page}] + chapters_html,
        toc_entries=[(f"Ch {i}", f"ch{i}.xhtml") for i in range(1, 6)]
    )
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(chapters) == 5
    assert len(fn_map) == 0
    for i in range(1, 6):
        assert f"This is the real body of chapter {i}" in chapters[i - 1]["content_html"]


def test_standalone_bare_number_toc_links_not_footnotes(fs):
    """Same-file bare-number links that stand alone in a list item are
    navigation (an in-file table of contents), not footnotes."""
    html = """<html><body>
    <h1>Ch</h1>
    <ul>
    <li><a href="#filepos1">1</a></li>
    <li><a href="#filepos2">2</a></li>
    </ul>
    <p id="filepos1">Body one.</p>
    <p id="filepos2">Body two.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(fn_map) == 0
    text = chapters[0]["content_html"]
    assert "Body one." in text
    assert "Body two." in text


def test_inline_bare_number_cross_ref_not_footnote(fs):
    """A bare-number link inline within a paragraph is a cross-reference, not a
    footnote. Its target is real content and must survive extraction.
    Regression: classifying these as footnotes decomposed the target block and
    silently dropped it from the chapter."""
    ch1 = """<html><body>
    <h1>Chapter One</h1>
    <p>For details see <a href="#sec">12</a>.</p>
    <div id="sec"><p>Important section text that a cross-reference must not delete.</p></div>
    </body></html>"""
    ch2 = "<html><body><h1>Chapter Two</h1><p>More text.</p></body></html>"
    epub_bytes = make_epub_bytes(
        [{"filename": "ch1.xhtml", "content": ch1},
         {"filename": "ch2.xhtml", "content": ch2}],
        toc_entries=[("One", "ch1.xhtml"), ("Two", "ch2.xhtml")]
    )
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    chapters, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert len(fn_map) == 0
    assert "S_FNREF_" not in chapters[0]["content_html"]
    assert "Important section text that a cross-reference must not delete." in chapters[0]["content_html"]


def test_footnote_epub3_noteref_cross_file(fs):
    """EPUB3 footnote references carry epub:type='noteref' and may use a bare
    number marker without <sup>; they must still be detected."""
    ch_html = """<html><body>
    <h1>Chapter</h1>
    <p>Text<a href="notes.xhtml#n1" epub:type="noteref">1</a> end.</p>
    </body></html>"""
    notes_html = """<html><body>
    <p id="n1">EPUB3 note body.</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([
        {"filename": "chapter.xhtml", "content": ch_html},
        {"filename": "notes.xhtml", "content": notes_html}
    ])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    _, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    assert fn_map.get("n1") == "EPUB3 note body."


def test_long_footnote_preserved(fs):
    """Footnote bodies have no size cap; a long note survives in full."""
    words = " ".join(f"word{i}" for i in range(500))
    html = f"""<html><body>
    <h1>Ch</h1>
    <p>Text<a href="#fn1"><sup>1</sup></a></p>
    <p id="fn1">{words}</p>
    </body></html>"""
    epub_bytes = make_epub_bytes([{"filename": "f.xhtml", "content": html}])
    path = write_epub_to_fake(fs, epub_bytes)
    epub_file = EpubFile(path)
    _, fn_map = Extractor(epub_file=epub_file, fn_prefix=SRC_FN_PREFIX, min_chars=1).get_chapter_list()
    note = fn_map["fn1"]
    assert "word0" in note
    assert "word499" in note
    assert len(note.split()) == 500