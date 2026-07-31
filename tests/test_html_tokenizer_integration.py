import pytest
from bbb.html_tokenizer import HtmlSentenceTokenizer
from bbb.splitter import Splitter
from conftest import create_chapter_html


@pytest.fixture
def tokenizer():
    splitter = Splitter("en")
    return HtmlSentenceTokenizer(splitter)


def test_chapter_with_verses(tokenizer):
    """Test that verses/poems are split line by line with each line indented."""
    html = create_chapter_html("Chapter 1", """
    <p>Regular paragraph.</p>
    <div class="poem">
    <p>Line one of poem.</p>
    <p>Line two of poem.</p>
    <p>Line three of poem.</p>
    </div>
    <p>Another paragraph.</p>
    """)
    sents, para_starts = tokenizer.extract(html, None)
    
    # Should have: 1 regular para sentence + 3 poem lines + 1 another para sentence = 5 sentences
    assert len(sents) == 5
    
    # Regular paragraph first sentence should be indented
    assert 0 in para_starts
    
    # Each poem line should be indented (first sentence of each line)
    assert 1 in para_starts
    assert 2 in para_starts
    assert 3 in para_starts
    
    # Another paragraph first sentence should be indented
    assert 4 in para_starts


def test_chapter_with_headings(tokenizer):
    """Test that headings are not indented."""
    html = create_chapter_html("Chapter 1", """
    <h1>Main Title</h1>
    <h2>Subtitle</h2>
    <p>First paragraph.</p>
    <p class="chapter-title">Chapter Title</p>
    <p>Second paragraph.</p>
    """)
    sents, para_starts = tokenizer.extract(html, None)
    
    # Headings should not be indented
    # The exact indices depend on how many sentences are extracted from each element
    # But we can check that heading elements themselves are not marked for indentation
    
    # Find which sentences come from headings
    heading_sentences = []
    for i, (text, fragment) in enumerate(sents):
        if '<h1>' in fragment or '<h2>' in fragment or 'chapter-title' in fragment:
            heading_sentences.append(i)
    
    # None of the heading sentences should be in para_starts
    for idx in heading_sentences:
        assert idx not in para_starts, f"Heading sentence at index {idx} should not be indented"


def test_complex_html_structure(tokenizer):
    """Test complex HTML with mixed content."""
    html = """
    <html><body>
    <h1>Chapter Title</h1>
    <p>First paragraph with <i>italic</i> text.</p>
    <p>Second paragraph.</p>
    <blockquote>
    <p>Quoted text.</p>
    </blockquote>
    <p>Third paragraph.</p>
    </body></html>
    """
    sents, para_starts = tokenizer.extract(html, None)
    
    # Should have multiple sentences
    assert len(sents) >= 4
    
    # First paragraph should be indented
    assert 0 in para_starts
    
    # HTML tags should be preserved
    for text, fragment in sents:
        if 'italic' in text:
            assert '<i>' in fragment


def test_preserves_original_html_structure(tokenizer):
    """Test that original HTML structure is preserved in fragments."""
    html = """
    <p><b>Bold</b> and <i>italic</i> text.</p>
    """
    sents, para_starts = tokenizer.extract(html, None)
    
    # Should have at least one sentence
    assert len(sents) >= 1
    
    # HTML tags should be preserved in fragments
    for text, fragment in sents:
        if 'Bold' in text:
            assert '<b>' in fragment
        if 'italic' in text:
            assert '<i>' in fragment


def test_br_tag_splitting(tokenizer):
    """Test that <br> tags create line breaks which split into separate sentences."""
    html = """
    <p>First line.<br/>Second line.<br/>Third line.</p>
    """
    sents, para_starts = tokenizer.extract(html, None)
    
    # Should have 3 sentences (one per line)
    assert len(sents) == 3
    
    # Each line should be indented
    assert 0 in para_starts
    assert 1 in para_starts
    assert 2 in para_starts


def test_empty_lines_skipped(tokenizer):
    """Test that empty lines are skipped."""
    html = """
    <p>First.</p>
    <p></p>
    <p>Second.</p>
    """
    sents, para_starts = tokenizer.extract(html, None)
    
    # Should have 2 sentences (empty paragraph skipped)
    assert len(sents) == 2
    assert 0 in para_starts
    assert 1 in para_starts
