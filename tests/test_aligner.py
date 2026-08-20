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


@pytest.mark.parametrize("src,tgt,result", [
    ("A. B. C.", "A. C.", [([0], [0]), ([2], [1])]),
    ("A. C.", "A. B. C.", [([0], [0]), ([1], [2])]),
])
def test_one_sentence_missing(src, tgt, result):
    """A sentence present on one side but missing on the other is interleaved
    as a one-sided row; the aligned sentences around it stay paired."""
    aligner = make_aligner(make_chapters([src]), make_chapters([tgt]))
    with patch.object(aligner.splitter, 'run', side_effect=lambda text, lang:
         [[s for s in text.split('. ')]]) as _, \
         patch('bbb.aligner.Bertalign') as mock_bert:
        mock_bert.return_value.result = result
        mock_bert.return_value.align_sents = MagicMock()
        out = aligner._align_pair(src, tgt, Language.ENGLISH, Language.GERMAN)
        assert len(out) == 3
        assert sent_text(out[0]['source_sents'][0]) == src.split('. ')[0]
        assert sent_text(out[0]['target_sents'][0]) == tgt.split('. ')[0]
        assert sent_text(out[2]['source_sents'][0]) == src.split('. ')[-1]
        assert sent_text(out[2]['target_sents'][0]) == tgt.split('. ')[-1]


@pytest.mark.parametrize("empty,full", [("", "Text"), ("Text", "")])
def test_empty_side_text(empty, full):
    aligner = make_aligner(make_chapters([empty]), make_chapters([full]))
    out = aligner._align_pair(empty, full, Language.ENGLISH, Language.GERMAN)
    assert len(out) == 1
    assert out[0]['source_sents'][0]['html'] == empty
    assert out[0]['target_sents'][0]['html'] == full


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
