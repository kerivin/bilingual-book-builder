import io, pytest, os
from unittest.mock import patch, MagicMock
from ebooklib import epub
import numpy as np
from bbb.bbb import BBB
from conftest import make_epub_bytes, create_chapter_html

LONG_TEXT = "This is a test chapter. " * 10

@pytest.fixture
def mock_heavy_deps():
    with patch('sentence_transformers.SentenceTransformer') as mock_st, \
         patch('bbb.splitter.SaT') as mock_sat, \
         patch('bbb.aligner.Bertalign') as mock_bert:
        # produce embeddings with consistent dimensionality
        def encode_side_effect(texts, **kwargs):
            n = len(texts)
            return np.eye(n, 2)
        mock_st.return_value.encode = MagicMock(side_effect=encode_side_effect)

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

def run_bbb_on_fake(fs, src_bytes, tgt_bytes, **kwargs):
    """Run BBB using a fake filesystem so BookBuilder can read real files."""
    fs.create_dir("/fake")
    src_path = "/fake/src.epub"
    tgt_path = "/fake/tgt.epub"
    out_path = "/fake/out.epub"
    fs.create_file(src_path, contents=src_bytes)
    fs.create_file(tgt_path, contents=tgt_bytes)

    bbb = BBB(source_path=src_path, target_path=tgt_path,
              output=out_path, verbosity='quiet', **kwargs)
    bbb.run()

    with open(out_path, 'rb') as f:
        return epub.read_epub(f)

def test_basic_bilingual_book(fs, mock_heavy_deps):
    src = make_epub_bytes([{"filename": "src.xhtml",
                            "content": create_chapter_html("Ch1", LONG_TEXT)}],
                          title="Source")
    tgt = make_epub_bytes([{"filename": "tgt.xhtml",
                            "content": create_chapter_html("Ch1", LONG_TEXT)}],
                          title="Target")
    book = run_bbb_on_fake(fs, src, tgt, source_language='en', target_language='de',
                           auto_threshold=0.5, simple_split=True)
    assert len(book.spine) == 1
    item = book.get_item_with_id(book.spine[0][0])
    content = item.get_content().decode()
    assert 'bilingual-table' in content
    assert 'This is a test chapter' in content

def test_keep_unmatched_source(fs, mock_heavy_deps):
    src = make_epub_bytes([
        {"filename": "s1.xhtml", "content": create_chapter_html("S1", LONG_TEXT)},
        {"filename": "s2.xhtml", "content": create_chapter_html("S2", LONG_TEXT)}
    ])
    tgt = make_epub_bytes([
        {"filename": "t1.xhtml", "content": create_chapter_html("T1", LONG_TEXT)}
    ])
    book = run_bbb_on_fake(fs, src, tgt, source_language='en', target_language='de',
                           keep_unmatched_source_chapters=True, simple_split=True)
    assert len(book.spine) == 2
    c0 = book.get_item_with_id(book.spine[0][0]).get_content().decode()
    c1 = book.get_item_with_id(book.spine[1][0]).get_content().decode()
    assert 'This is a test chapter' in c0
    assert 'bilingual-source-only' in c1

def test_cover_option_target(fs, mock_heavy_deps):
    src = make_epub_bytes([{"filename": "s.xhtml", "content": create_chapter_html("Src", LONG_TEXT)}],
                          title="Src", cover=True)
    tgt = make_epub_bytes([{"filename": "c.xhtml", "content": create_chapter_html("Tgt", LONG_TEXT)}],
                          title="Tgt", cover=True)
    book = run_bbb_on_fake(fs, src, tgt, cover='target', simple_split=True)
    assert book.get_item_with_id('cover') is not None

def test_only_extract(fs, mock_heavy_deps):
    src = make_epub_bytes([{"filename": "s.xhtml",
                            "content": create_chapter_html("S", LONG_TEXT)}])
    tgt = make_epub_bytes([{"filename": "t.xhtml",
                            "content": create_chapter_html("T", LONG_TEXT)}])
    fs.create_dir("/fake")
    src_path = "/fake/src.epub"
    tgt_path = "/fake/tgt.epub"
    fs.create_file(src_path, contents=src)
    fs.create_file(tgt_path, contents=tgt)

    with patch('bbb.bbb.epub.write_epub') as mock_write:
        bbb = BBB(source_path=src_path, target_path=tgt_path, only='extract',
                   verbosity='quiet')
        bbb.run()
    mock_write.assert_not_called()