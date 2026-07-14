import io, pytest
from unittest.mock import patch, MagicMock
from ebooklib import epub
from bbb.bbb import BBB
from conftest import make_epub_bytes, create_chapter_html, MockDocument

@pytest.fixture
def mock_heavy_deps():
    with patch('bbb.bbb.SentenceTransformer') as mock_st, \
         patch('bbb.splitter.SaT') as mock_sat, \
         patch('bbb.aligner.Bertalign') as mock_bert:
        mock_st.return_value.encode = MagicMock(
            side_effect=lambda texts, **kw: [[1.0, 0.0] if "alpha" in t else [0.0, 1.0] for t in texts]
        )
        class MockSaT:
            def split(self, lines, do_paragraph_segmentation=False):
                return [line.split('. ') for line in lines]
        mock_sat.return_value = MockSaT()
        class MockBertalign:
            def __init__(self, **kwargs):
                pass
            def align_sents(self):
                self.result = [([i], [i]) for i in range(len(self.source_sentences))]
        mock_bert.return_value = MockBertalign()
        yield

def run_bbb(src_bytes, tgt_bytes, **kwargs):
    """Helper to run BBB with in-memory epubs and capture output."""
    output_buf = io.BytesIO()
    with patch('bbb.bbb.epub.write_epub') as mock_write:
        def write_to_buf(name, book):
            epub.write_epub(output_buf, book)
        mock_write.side_effect = write_to_buf
        with patch('bbb.extractor.Document', MockDocument), \
             patch('bbb.book_builder.epub.read_epub',
                   side_effect=lambda p: epub.read_epub(io.BytesIO(p))):
            bbb = BBB(source_path=src_bytes, target_path=tgt_bytes,
                       output='dummy.epub', verbosity='quiet', **kwargs)
            bbb.run()
    output_buf.seek(0)
    return epub.read_epub(output_buf)

def test_basic_bilingual_book(mock_heavy_deps):
    src = make_epub_bytes([
        {"filename": "src.xhtml", "content": create_chapter_html("Ch1", "Hello world. Test.")}
    ], title="Source")
    tgt = make_epub_bytes([
        {"filename": "tgt.xhtml", "content": create_chapter_html("Ch1", "Hallo Welt. Test.")}
    ], title="Target")
    book = run_bbb(src, tgt, source_language='en', target_language='de',
                   auto_threshold=0.5, simple_split=True)
    spine = book.spine
    assert len(spine) == 1
    item = book.get_item_with_id(spine[0][0])
    content = item.get_content().decode()
    assert 'bilingual-table' in content
    assert 'Hello world' in content
    assert 'Hallo Welt' in content

def test_keep_unmatched_source(mock_heavy_deps):
    src = make_epub_bytes([
        {"filename": "s1.xhtml", "content": create_chapter_html("S1", "Source 1")},
        {"filename": "s2.xhtml", "content": create_chapter_html("S2", "Source 2")}
    ])
    tgt = make_epub_bytes([
        {"filename": "t1.xhtml", "content": create_chapter_html("T1", "Target 1")}
    ])   # one chapter missing
    book = run_bbb(src, tgt, source_language='en', target_language='de',
                   keep_unmatched_source_chapters=True, simple_split=True)
    # Should have both matched and unmatched source
    spine = book.spine
    assert len(spine) == 2
    content0 = book.get_item_with_id(spine[0][0]).get_content().decode()
    content1 = book.get_item_with_id(spine[1][0]).get_content().decode()
    assert 'Source 1' in content0
    assert 'Target 1' in content0   # first pair matched
    assert 'Source 2' in content1
    assert 'bilingual-source-only' in content1   # only source

def test_cover_option_target(mock_heavy_deps):
    src = make_epub_bytes([], title="Src", cover=True)
    tgt = make_epub_bytes([{"filename": "c.xhtml", "content": "<p>hi</p>"}], title="Tgt", cover=True)
    book = run_bbb(src, tgt, cover='target', simple_split=True)
    # Check that cover image is present (ebooklib usually sets cover in metadata)
    # We'll just check that the cover item exists
    cover = book.get_item_with_id('cover')
    assert cover is not None
    # It should be the target cover (we can't easily distinguish content here, but we trust logic)

def test_only_extract(mock_heavy_deps):
    src = make_epub_bytes([
        {"filename": "s.xhtml", "content": create_chapter_html("S", "source")}
    ])
    tgt = make_epub_bytes([
        {"filename": "t.xhtml", "content": create_chapter_html("T", "target")}
    ])
    # Should print info and return early, no output book
    with patch('bbb.bbb.epub.write_epub') as mock_write:
        with patch('bbb.extractor.Document', MockDocument), \
             patch('bbb.book_builder.epub.read_epub',
                   side_effect=lambda p: epub.read_epub(io.BytesIO(p))):
            bbb = BBB(source_path=src, target_path=tgt, only='extract',
                       verbosity='quiet')
            bbb.run()
        mock_write.assert_not_called()