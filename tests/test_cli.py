import sys
from unittest.mock import patch

from bbb import cli


def run_cli(fs, *argv):
    with patch.object(sys, 'argv', ['bbb', *argv]):
        return cli.main()


def test_main_returns_nonzero_when_input_file_missing(fs):
    assert run_cli(fs, '-s', '/fake/missing.epub', '-t', '/fake/also-missing.epub',
                   '-v', 'silent') == 1
