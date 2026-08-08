import pytest
from bs4 import BeautifulSoup
from bbb.html_tokenizer import HtmlSentenceTokenizer, Language
from bbb.splitter import Splitter
from conftest import create_chapter_html


@pytest.fixture
def tokenizer():
    splitter = Splitter(None)
    return HtmlSentenceTokenizer(splitter)


def test_basic_paragraph(tokenizer):
    html = "<p>Hello world. This is a test.</p>"
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 2
    assert 0 in para_starts
    assert "Hello world." in sents[0][0]
    assert "This is a test." in sents[1][0]


def test_multiple_paragraphs(tokenizer):
    html = "<p>First paragraph.</p><p>Second paragraph.</p>"
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 2
    assert 0 in para_starts
    assert 1 in para_starts


def test_heading_not_indented(tokenizer):
    html = "<h1>Chapter 1</h1><p>First sentence.</p>"
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    # Heading should not be marked for indentation
    assert 0 not in para_starts
    # First paragraph sentence should be marked
    assert 1 in para_starts


def test_heading_with_class_not_indented(tokenizer):
    html = "<h1 class='chapter'>Chapter Title</h1><p>First sentence.</p>"
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    # Heading should not be indented
    assert 0 not in para_starts
    # First paragraph sentence should be marked
    assert 1 in para_starts


def test_verse_poem_line_by_line(tokenizer):
    html = """<div class="poem">
    <p>Line one.</p>
    <p>Line two.</p>
    <p>Line three.</p>
    </div>"""
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    # Each line should be treated as a separate paragraph
    assert len(sents) == 3
    assert 0 in para_starts
    assert 1 in para_starts
    assert 2 in para_starts


def test_br_placeholder_handling(tokenizer):
    html = "<p>First line.<br/>Second line.</p>"
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    # BR should be replaced with newline, splitting into separate lines
    assert len(sents) >= 2


def test_html_fragments_preserved(tokenizer):
    html = "<p><i>Italic text.</i> Normal text.</p>"
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 2
    # Check that HTML tags are preserved in fragments
    assert "<i>" in sents[0][1]
    assert "</i>" in sents[0][1]


def test_empty_html(tokenizer):
    html = ""
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 0


def test_whitespace_handling(tokenizer):
    html = "<p>  Hello   world.  </p>"
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 1
    assert "Hello world." in sents[0][0]


def test_nested_blocks(tokenizer):
    html = """<div>
    <p>First paragraph.</p>
    <p>Second paragraph.</p>
    </div>"""
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 2
    assert 0 in para_starts
    assert 1 in para_starts


def test_blockquote_handling(tokenizer):
    html = """<blockquote>
    <p>Quoted text.</p>
    </blockquote>"""
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 1
    assert 0 in para_starts


def test_div_paragraphs_get_indented(tokenizer):
    html = """<div class="pi">First paragraph first sentence. First paragraph second sentence.</div>
<div class="pi">Second paragraph first sentence.</div>"""
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 3
    # First sentence of each div should be marked
    assert 0 in para_starts
    assert 1 not in para_starts
    assert 2 in para_starts


def test_poem_lines_in_single_block_only_first_indented(tokenizer):
    html = """<blockquote><div>
    <br/>Line one.<br/>Line two.<br/>Line three.<br/>
    </div></blockquote>"""
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 3
    # One block is a single paragraph: only the first line is a paragraph start
    assert 0 in para_starts
    assert 1 not in para_starts
    assert 2 not in para_starts


def test_multiline_div_only_first_sentence_indented(tokenizer):
    html = """<div class="pi">First sentence of paragraph.
Second sentence of paragraph.</div>"""
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 2
    assert 0 in para_starts
    assert 1 not in para_starts


def test_soft_wrapped_lines_are_joined(tokenizer):
    html = ("<p>They could not understand the conduct of this\n"
            "rustic fiddler, who tramped the roads with that\n"
            "pretty child who sang like an angel from Heaven.\n"
            "A second sentence.</p>")
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert [sent[0] for sent in sents] == [
        "They could not understand the conduct of this rustic fiddler, who tramped the roads with that pretty child who sang like an angel from Heaven.",
        "A second sentence.",
    ]
    assert 0 in para_starts


def test_hyphenated_soft_wrapped_words_are_joined(tokenizer):
    html = ("<p>This is a sufficiently long line to identify the source as\n"
            "print-wrapped text with a rus-\n"
            "tic fiddler who tramped the roads.</p>")
    sents, _ = tokenizer.extract(html, Language.ENGLISH)
    assert sents[0][0] == "This is a sufficiently long line to identify the source as print-wrapped text with a rustic fiddler who tramped the roads."
    assert "rus-" not in sents[0][1]


def test_newline_separated_sentences_remain_split(tokenizer):
    html = "<p>First sentence.\nSecond sentence.</p>"
    sents, _ = tokenizer.extract(html, Language.ENGLISH)
    assert [sent[0] for sent in sents] == ["First sentence.", "Second sentence."]


def test_processing_instruction_in_heading(tokenizer):
    html = '<h2 class="head" id="h"><?pagebreak number="1"?><a id="p1"/>One</h2>'
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 1
    assert sents[0][0] == "One"
    assert "pagebreak" not in sents[0][1].lower()
    assert "One" in sents[0][1]


def test_processing_instruction_in_paragraph(tokenizer):
    html = ('<p>Immediately she told my mother in bad French '
            '<?pagebreak number="8"?><a id="p8"/>a pointless and quite '
            'irrelevant story about a Polish woman.</p>')
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 1
    assert "pagebreak" not in sents[0][1].lower()
    assert sents[0][0] == ("Immediately she told my mother in bad French "
                           "a pointless and quite irrelevant story about a Polish woman.")


def test_comment_not_in_fragment(tokenizer):
    html = "<p>Hello <!--mid comment-->world.</p>"
    sents, para_starts = tokenizer.extract(html, Language.ENGLISH)
    assert len(sents) == 1
    assert sents[0][0] == "Hello world."
    assert "comment" not in sents[0][1]
