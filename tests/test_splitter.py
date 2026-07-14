# tests/test_splitter.py
import pytest
from unittest.mock import patch
from lingua import Language
from bbb.splitter import SimpleWrapper, SatWrapper

def test_simple_splitter_paragraphs():
    s = SimpleWrapper(Language.ENGLISH)
    text = "Hello. World.\n\nSecond paragraph."
    result = s.split(text)
    assert len(result) == 2
    assert result[0] == ["Hello.", "World."]
    assert result[1] == ["Second paragraph."]

def test_simple_splitter_multiline_paragraph():
    s = SimpleWrapper(Language.ENGLISH)
    text = "Line one.\nLine two.\n\nAnother paragraph."
    result = s.split(text)
    assert len(result) == 2
    assert result[0] == ["Line one.", "Line two."]
    assert result[1] == ["Another paragraph."]

def test_simple_splitter_empty_text():
    s = SimpleWrapper(Language.ENGLISH)
    assert s.split("") == []

def test_simple_splitter_only_newlines():
    s = SimpleWrapper(Language.ENGLISH)
    assert s.split("\n\n\n") == []

def test_sat_wrapper_mocked():
    with patch('bbb.splitter.SaT') as mock_sat:
        # SaT constructor won't be called because we patched it
        sw = SatWrapper(Language.ENGLISH, "fake-model")
        # Replace the splitter with a fake one that just returns lines as sentences
        class FakeSat:
            def split(self, lines, do_paragraph_segmentation=False):
                return [[line] for line in lines]
        sw.splitter = FakeSat()
        text = "A.\nB.\n\nC."
        result = sw.split(text)
        assert result == [["A.", "B."], ["C."]]