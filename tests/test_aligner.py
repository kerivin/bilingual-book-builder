import pytest
from unittest.mock import MagicMock, patch
from bs4 import BeautifulSoup
from lingua import Language
from bbb.aligner import Aligner


def make_chapters(texts):
    return [{"toc_path": [f"Chapter {i}"], "content_html": t, "index": i,
             "footnote_refs": [], "body_class": ""} for i, t in enumerate(texts)]


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


def sent_text(sent):
    html = sent['html'].replace('<[document]>', '').replace('</[document]>', '')
    return BeautifulSoup(html, 'html.parser').get_text()


def test_perfect_one_to_one():
    aligner = make_aligner(make_chapters(["Hello. World."]), make_chapters(["Hallo. Welt."]))
    with patch.object(aligner.splitter, 'run', side_effect=lambda text, lang:
        [["Hello.", "World."]] if lang == Language.ENGLISH else [["Hallo.", "Welt."]]), \
         patch('bbb.aligner.Bertalign') as mock_bert:
        mock_bert.return_value.result = [([0], [0]), ([1], [1])]
        mock_bert.return_value.align_sents = MagicMock()
        result = aligner._align_pair("Hello. World.", "Hallo. Welt.",
                                     Language.ENGLISH, Language.GERMAN)
        assert len(result) == 2
        assert sent_text(result[0]['source_sents'][0]) == 'Hello.'
        assert sent_text(result[0]['target_sents'][0]) == 'Hallo.'
        assert sent_text(result[1]['source_sents'][0]) == 'World.'
        assert sent_text(result[1]['target_sents'][0]) == 'Welt.'


def test_sentences_missing_in_target():
    aligner = make_aligner(make_chapters(["A. B. C."]), make_chapters(["A. C."]))
    with patch.object(aligner.splitter, 'run', side_effect=lambda text, lang:
        [["A.", "B.", "C."]] if lang == Language.ENGLISH else [["A.", "C."]]), \
         patch('bbb.aligner.Bertalign') as mock_bert:
        mock_bert.return_value.result = [([0], [0]), ([2], [1])]
        mock_bert.return_value.align_sents = MagicMock()
        result = aligner._align_pair("A. B. C.", "A. C.",
                                     Language.ENGLISH, Language.GERMAN)
        assert len(result) == 3
        assert sent_text(result[0]['source_sents'][0]) == 'A.'
        assert sent_text(result[0]['target_sents'][0]) == 'A.'
        assert sent_text(result[1]['source_sents'][0]) == 'B.'
        assert result[1]['target_sents'] == []
        assert sent_text(result[2]['source_sents'][0]) == 'C.'
        assert sent_text(result[2]['target_sents'][0]) == 'C.'


def test_sentences_missing_in_source():
    aligner = make_aligner(make_chapters(["A. C."]), make_chapters(["A. B. C."]))
    with patch.object(aligner.splitter, 'run', side_effect=lambda text, lang:
        [["A.", "C."]] if lang == Language.ENGLISH else [["A.", "B.", "C."]]), \
         patch('bbb.aligner.Bertalign') as mock_bert:
        mock_bert.return_value.result = [([0], [0]), ([1], [2])]
        mock_bert.return_value.align_sents = MagicMock()
        result = aligner._align_pair("A. C.", "A. B. C.",
                                     Language.ENGLISH, Language.GERMAN)
        assert len(result) == 3
        assert sent_text(result[0]['source_sents'][0]) == 'A.'
        assert sent_text(result[0]['target_sents'][0]) == 'A.'
        assert result[1]['source_sents'] == []
        assert sent_text(result[1]['target_sents'][0]) == 'B.'
        assert sent_text(result[2]['source_sents'][0]) == 'C.'
        assert sent_text(result[2]['target_sents'][0]) == 'C.'


def test_empty_source_text():
    aligner = make_aligner(make_chapters([""]), make_chapters(["Text"]))
    result = aligner._align_pair("", "Text", Language.ENGLISH, Language.GERMAN)
    assert len(result) == 1
    assert result[0]['source_sents'][0]['html'] == ''
    assert sent_text(result[0]['target_sents'][0]) == 'Text'


def test_empty_target_text():
    aligner = make_aligner(make_chapters(["Text"]), make_chapters([""]))
    result = aligner._align_pair("Text", "", Language.ENGLISH, Language.GERMAN)
    assert len(result) == 1
    assert sent_text(result[0]['source_sents'][0]) == 'Text'
    assert result[0]['target_sents'][0]['html'] == ''


def test_heading_is_not_paragraph_start():
    aligner = make_aligner(make_chapters(["<h1>Title</h1><p>Body.</p>"]),
                           make_chapters(["<h1>Titel</h1><p>Text.</p>"]))
    with patch.object(aligner.splitter, 'run', side_effect=lambda text, lang:
        [[s] for s in text.split('. ')]), \
         patch('bbb.aligner.Bertalign') as mock_bert:
        mock_bert.return_value.result = [([0], [0]), ([1], [1])]
        mock_bert.return_value.align_sents = MagicMock()
        result = aligner._align_pair("<h1>Title</h1><p>Body.</p>",
                                     "<h1>Titel</h1><p>Text.</p>",
                                     Language.ENGLISH, Language.GERMAN)
        # Heading sentence is not a paragraph start; the body paragraph is.
        assert result[0]['source_sents'][0]['first'] is False
        assert result[1]['source_sents'][0]['first'] is True


def test_bertalign_reexports_work_together():
    from bertalign import Bertalign
    from bertalign.encoder import Encoder
    from bertalign.corelib import second_back_track
    from bertalign.utils import yield_overlaps
    assert Bertalign is not None
    assert Encoder is not None
    assert callable(second_back_track)
    assert list(yield_overlaps(["a", "b"], 2))


def test_invalid_language_code_does_not_crash():
    aligner = make_aligner(make_chapters(["Hello."]), make_chapters(["Hallo."]),
                           source_lang="xx", target_lang="yy")
    assert aligner.source_language is None
    assert aligner.target_language is None


def test_insertion_deletion_beads_do_not_crash():
    aligner = make_aligner(make_chapters(["A. B. C. D. E."]),
                           make_chapters(["A. X. C. D."]))
    with patch.object(aligner.splitter, 'run', side_effect=lambda text, lang:
        [["A.", "B.", "C.", "D.", "E."]] if lang == Language.ENGLISH
        else [["A.", "X.", "C.", "D."]]), \
         patch('bbb.aligner.Bertalign') as mock_bert:
        mock_bert.return_value.result = [([0], [0]), ([], [1]), ([2], [2]), ([3], [3])]
        mock_bert.return_value.align_sents = MagicMock()
        result = aligner._align_pair("A. B. C. D. E.", "A. X. C. D.",
                                     Language.ENGLISH, Language.GERMAN)
        assert len(result) == 6
        assert sent_text(result[0]['source_sents'][0]) == 'A.'
        assert result[1]['target_sents'] == []
        assert sent_text(result[1]['source_sents'][0]) == 'B.'
        assert result[2]['source_sents'] == []
        assert sent_text(result[2]['target_sents'][0]) == 'X.'
        assert sent_text(result[3]['source_sents'][0]) == 'C.'
        assert sent_text(result[4]['source_sents'][0]) == 'D.'
        assert result[5]['target_sents'] == []
        assert sent_text(result[5]['source_sents'][0]) == 'E.'
