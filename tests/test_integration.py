import pytest
from unittest.mock import patch, MagicMock
from ebooklib import epub
import numpy as np
from bbb.bbb import BBB
from conftest import make_epub_bytes, create_chapter_html, write_epub_to_fake


LONG_TEXT = "This is a test chapter. " * 10


@pytest.fixture
def mock_heavy_deps():
    with patch('sentence_transformers.SentenceTransformer') as mock_st, \
         patch('bbb.splitter.SaT') as mock_sat, \
         patch('bbb.aligner.Bertalign') as mock_bert:
        # produce embeddings with consistent dimensionality
        def encode_side_effect(texts, **kwargs):
            n = len(texts)
            return np.eye(n, 2)   # always 2‑dim
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


def test_basic_bilingual_book(fs, mock_heavy_deps):
    src_bytes = make_epub_bytes([{"filename": "src.xhtml",
                                  "content": create_chapter_html("Ch1", LONG_TEXT)}],
                                title="Source")
    tgt_bytes = make_epub_bytes([{"filename": "tgt.xhtml",
                                  "content": create_chapter_html("Ch1", LONG_TEXT)}],
                                title="Target")
    src_path = write_epub_to_fake(fs, src_bytes, "src.epub")
    tgt_path = write_epub_to_fake(fs, tgt_bytes, "tgt.epub")
    out_path = "/fake/out.epub"

    bbb = BBB(source_path=src_path, target_path=tgt_path,
              output=out_path, verbosity='quiet',
              source_language='en', target_language='de',
              auto_threshold=0.5, simple_split=True)
    bbb.run()

    with open(out_path, 'rb') as f:
        book = epub.read_epub(f)
    assert len(book.spine) == 1
    item = book.get_item_with_id(book.spine[0][0])
    content = item.get_content().decode()
    assert 'bilingual-table' in content
    assert 'This is a test chapter' in content


def test_keep_unmatched_source(fs, mock_heavy_deps):
    src_bytes = make_epub_bytes([
        {"filename": "s1.xhtml", "content": create_chapter_html("S1", LONG_TEXT)},
        {"filename": "s2.xhtml", "content": create_chapter_html("S2", LONG_TEXT)}
    ])
    tgt_bytes = make_epub_bytes([
        {"filename": "t1.xhtml", "content": create_chapter_html("T1", LONG_TEXT)}
    ])
    src_path = write_epub_to_fake(fs, src_bytes, "src.epub")
    tgt_path = write_epub_to_fake(fs, tgt_bytes, "tgt.epub")
    out_path = "/fake/out.epub"

    bbb = BBB(source_path=src_path, target_path=tgt_path,
              output=out_path, verbosity='quiet',
              source_language='en', target_language='de',
              keep_unmatched_source_chapters=True, simple_split=True)
    bbb.run()

    with open(out_path, 'rb') as f:
        book = epub.read_epub(f)
    assert len(book.spine) == 2
    c0 = book.get_item_with_id(book.spine[0][0]).get_content().decode()
    c1 = book.get_item_with_id(book.spine[1][0]).get_content().decode()
    assert 'This is a test chapter' in c0
    assert 'bilingual-source-only' in c1


def test_cover_option_target(fs, mock_heavy_deps):
    src_bytes = make_epub_bytes([{"filename": "s.xhtml",
                                  "content": create_chapter_html("Src", LONG_TEXT)}],
                                title="Src", cover=True)
    tgt_bytes = make_epub_bytes([{"filename": "t.xhtml",
                                  "content": create_chapter_html("Tgt", LONG_TEXT)}],
                                title="Tgt", cover=True)
    src_path = write_epub_to_fake(fs, src_bytes, "src.epub")
    tgt_path = write_epub_to_fake(fs, tgt_bytes, "tgt.epub")
    out_path = "/fake/out.epub"

    bbb = BBB(source_path=src_path, target_path=tgt_path,
              output=out_path, verbosity='quiet',
              cover='target', simple_split=True)
    bbb.run()

    with open(out_path, 'rb') as f:
        book = epub.read_epub(f)
    assert book.get_item_with_id('cover') is not None


def test_only_extract(fs, mock_heavy_deps):
    src_bytes = make_epub_bytes([{"filename": "s.xhtml",
                                  "content": create_chapter_html("S", LONG_TEXT)}])
    tgt_bytes = make_epub_bytes([{"filename": "t.xhtml",
                                  "content": create_chapter_html("T", LONG_TEXT)}])
    src_path = write_epub_to_fake(fs, src_bytes, "src.epub")
    tgt_path = write_epub_to_fake(fs, tgt_bytes, "tgt.epub")

    with patch('bbb.bbb.epub.write_epub') as mock_write:
        bbb = BBB(source_path=src_path, target_path=tgt_path,
                  only='extract', verbosity='quiet')
        bbb.run()
    mock_write.assert_not_called()