import pytest
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
    # One paragraph with two sentences (line one. + line two.)
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
    class FakeSat:
        def split(self, lines, do_paragraph_segmentation=False):
            return [[line] for line in lines]
    sw = SatWrapper(Language.ENGLISH, "fake")
    sw.splitter = FakeSat()
    text = "A.\nB.\n\nC."
    result = sw.split(text)
    assert result == [["A.", "B."], ["C."]]