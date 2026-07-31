import pytest
from bs4 import BeautifulSoup
from bbb.html_tokenizer import HtmlSentenceTokenizer
from bbb.splitter import Splitter
from conftest import create_chapter_html


@pytest.fixture
def tokenizer():
    splitter = Splitter("en")
    return HtmlSentenceTokenizer(splitter)


def test_basic_paragraph(tokenizer):
    html = "<p>Hello world. This is a test.</p>"
    sents, para_starts = tokenizer.extract(html, None)
    assert len(sents) == 2
    assert 0 in para_starts
    assert "Hello world." in sents[0][0]
    assert "This is a test." in sents[1][0]


def test_multiple_paragraphs(tokenizer):
    html = "<p>First paragraph.</p><p>Second paragraph.</p>"
    sents, para_starts = tokenizer.extract(html, None)
    assert len(sents) == 2
    assert 0 in para_starts
    assert 1 in para_starts


def test_heading_not_indented(tokenizer):
    html = "<h1>Chapter 1</h1><p>First sentence.</p>"
    sents, para_starts = tokenizer.extract(html, None)
    # Heading should not be marked for indentation
    assert 0 not in para_starts
    # First paragraph sentence should be marked
    assert 1 in para_starts


def test_heading_with_class_not_indented(tokenizer):
    html = "<p class='chapter'>Chapter Title</p><p>First sentence.</p>"
    sents, para_starts = tokenizer.extract(html, None)
    # Chapter title should not be indented
    assert 0 not in para_starts
    # First paragraph sentence should be marked
    assert 1 in para_starts


def test_verse_poem_line_by_line(tokenizer):
    html = """<div class="poem">
    <p>Line one.</p>
    <p>Line two.</p>
    <p>Line three.</p>
    </div>"""
    sents, para_starts = tokenizer.extract(html, None)
    # Each line should be treated as a separate paragraph
    assert len(sents) == 3
    assert 0 in para_starts
    assert 1 in para_starts
    assert 2 in para_starts


def test_br_placeholder_handling(tokenizer):
    html = "<p>First line.<br/>Second line.</p>"
    sents, para_starts = tokenizer.extract(html, None)
    # BR should be replaced with newline, splitting into separate lines
    assert len(sents) >= 2


def test_html_fragments_preserved(tokenizer):
    html = "<p><i>Italic text.</i> Normal text.</p>"
    sents, para_starts = tokenizer.extract(html, None)
    assert len(sents) == 2
    # Check that HTML tags are preserved in fragments
    assert "<i>" in sents[0][1]
    assert "</i>" in sents[0][1]


def test_empty_html(tokenizer):
    html = ""
    sents, para_starts = tokenizer.extract(html, None)
    assert len(sents) == 0


def test_whitespace_handling(tokenizer):
    html = "<p>  Hello   world.  </p>"
    sents, para_starts = tokenizer.extract(html, None)
    assert len(sents) == 1
    assert "Hello world." in sents[0][0]


def test_nested_blocks(tokenizer):
    html = """<div>
    <p>First paragraph.</p>
    <p>Second paragraph.</p>
    </div>"""
    sents, para_starts = tokenizer.extract(html, None)
    assert len(sents) == 2
    assert 0 in para_starts
    assert 1 in para_starts


def test_blockquote_handling(tokenizer):
    html = """<blockquote>
    <p>Quoted text.</p>
    </blockquote>"""
    sents, para_starts = tokenizer.extract(html, None)
    assert len(sents) == 1
    assert 0 in para_starts
