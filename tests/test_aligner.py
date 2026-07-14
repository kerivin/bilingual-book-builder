import pytest
from unittest.mock import MagicMock, patch
from lingua import Language
from bbb.aligner import Aligner

def make_chapters(texts):
    return [{"full_text": t, "footnote_refs": [], "index": i} for i, t in enumerate(texts)]

def make_aligner(src_chapters, tgt_chapters, chapter_pairs=None,
                  source_lang="en", target_lang="de", threads=1, split_model=None):
    st_model = MagicMock()
    if chapter_pairs is None:
        chapter_pairs = [(i, i) for i in range(len(src_chapters))]
    return Aligner(
        source_chapters=src_chapters,
        target_chapters=tgt_chapters,
        chapter_pairs=chapter_pairs,
        source_language=source_lang,
        target_language=target_lang,
        threads=threads,
        align_model=st_model,
        split_model=split_model
    )

def test_perfect_one_to_one():
    aligner = make_aligner(make_chapters(["Hello. World."]), make_chapters(["Hallo. Welt."]))
    with patch.object(aligner.splitter, 'run', side_effect=lambda text, lang:
        [["Hello.", "World."]] if lang == Language.ENGLISH else [["Hallo.", "Welt."]]), \
         patch('bbb.aligner.Bertalign') as mock_bert:
        mock_bert.return_value.result = [([0], [0]), ([1], [1])]
        mock_bert.return_value.align_sents = MagicMock()
        result = aligner._align_pair("Hello. World.", "Hallo. Welt.")
        assert len(result) == 1
        para = result[0]
        assert len(para) == 2
        assert para[0]['source'] == 'Hello.'
        assert para[0]['target'] == 'Hallo.'
        assert para[1]['source'] == 'World.'
        assert para[1]['target'] == 'Welt.'

def test_sentences_missing_in_target():
    aligner = make_aligner(make_chapters(["A. B. C."]), make_chapters(["A. C."]))
    with patch.object(aligner.splitter, 'run', side_effect=lambda text, lang:
        [["A.", "B.", "C."]] if lang == Language.ENGLISH else [["A.", "C."]]), \
         patch('bbb.aligner.Bertalign') as mock_bert:
        mock_bert.return_value.result = [([0], [0]), ([2], [1])]
        mock_bert.return_value.align_sents = MagicMock()
        result = aligner._align_pair("A. B. C.", "A. C.")
        para = result[0]
        assert len(para) == 3
        assert para[0]['source'] == 'A.' and para[0]['target'] == 'A.'
        assert para[1]['source'] == 'B.' and para[1]['target'] == ''
        assert para[2]['source'] == 'C.' and para[2]['target'] == 'C.'

def test_sentences_missing_in_source():
    aligner = make_aligner(make_chapters(["A. C."]), make_chapters(["A. B. C."]))
    with patch.object(aligner.splitter, 'run', side_effect=lambda text, lang:
        [["A.", "C."]] if lang == Language.ENGLISH else [["A.", "B.", "C."]]), \
         patch('bbb.aligner.Bertalign') as mock_bert:
        mock_bert.return_value.result = [([0], [0]), ([1], [2])]
        mock_bert.return_value.align_sents = MagicMock()
        result = aligner._align_pair("A. C.", "A. B. C.")
        para = result[0]
        assert len(para) == 3
        assert para[0]['source'] == 'A.' and para[0]['target'] == 'A.'
        assert para[1]['source'] == '' and para[1]['target'] == 'B.'
        assert para[2]['source'] == 'C.' and para[2]['target'] == 'C.'

def test_empty_source_text():
    aligner = make_aligner(make_chapters([""]), make_chapters(["Text"]))
    result = aligner._align_pair("", "Text")
    assert result == []

def test_empty_target_text():
    aligner = make_aligner(make_chapters(["Text"]), make_chapters([""]))
    result = aligner._align_pair("Text", "")
    assert result == []