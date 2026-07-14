import numpy as np
import pytest
from unittest.mock import MagicMock
from bbb.mapper import Mapper

def make_chapters(titles, texts):
    return [{"toc_path": [t], "full_text": txt, "preview": txt[:30], "index": i}
            for i, (t, txt) in enumerate(zip(titles, texts))]

@pytest.fixture
def mock_model():
    model = MagicMock()
    return model

def test_perfect_match(mock_model):
    src = make_chapters(["A", "B"], ["alpha", "beta"])
    tgt = make_chapters(["A'", "B'"], ["alpha", "beta"])
    mock_model.encode.return_value = np.array([[1.0, 0.0], [0.0, 1.0]])
    mapper = Mapper(src, tgt, False, False)
    result = mapper.run_auto(mock_model, force_show=False, threshold=0.5)
    assert result == [(0,0), (1,1)]

def test_partial_match_some_unmatched(mock_model):
    src = make_chapters(["A", "B", "C"], ["aa", "bb", "cc"])
    tgt = make_chapters(["A'", "C'"], ["aa", "cc"])
    # Simulate similarities: high for (0,0) and (2,1), low else
    sim = np.array([[1.0, 0.0], [0.0, 0.0], [0.0, 1.0]])
    mock_model.encode.side_effect = lambda texts, **kw: np.array([
        [1.0, 0.0], [0.0, 0.0], [0.0, 1.0]
    ][:len(texts)])
    mapper = Mapper(src, tgt, keep_unmatched_source_chapters=False,
                    keep_unmatched_target_chapters=False)
    result = mapper.run_auto(mock_model, force_show=False, threshold=0.5)
    # Expected pairs: (0,0) matched, B unmatched (skipped), C matched with target 1
    # The DP should give: (0,0), then source 1 unmatched, then (2,1)
    assert result == [(0,0), (1, None), (2,1)]

def test_no_match_above_threshold(mock_model):
    src = make_chapters(["X"], ["x"])
    tgt = make_chapters(["Y"], ["y"])
    mock_model.encode.return_value = np.array([[0.1]])
    mapper = Mapper(src, tgt, keep_unmatched_source_chapters=True,
                    keep_unmatched_target_chapters=True)
    result = mapper.run_auto(mock_model, force_show=False, threshold=0.8)
    # Both unmatched, output: source, target
    assert result == [(0, None), (None, 0)]

def test_empty_source(mock_model):
    src = make_chapters([], [])
    tgt = make_chapters(["A"], ["alpha"])
    mapper = Mapper(src, tgt, False, False)
    result = mapper.run_auto(mock_model, force_show=False, threshold=0.5)
    assert result == [(None, 0)]

def test_empty_target(mock_model):
    src = make_chapters(["A"], ["alpha"])
    tgt = make_chapters([], [])
    mapper = Mapper(src, tgt, False, False)
    result = mapper.run_auto(mock_model, force_show=False, threshold=0.5)
    assert result == [(0, None)]