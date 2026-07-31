Don't write comments in the code, but you can explain your solution outside of it.
This is a project that creates a parallel text with sentences aligned side-by-side from the book's original and translation EPUB files. I'm trying to preserve the original HTML to some capacity, not all of it (if I keep all of tags, they could break the table, for example with extra margins), but italics, bold, alignment, headings etc styles, which are the tags that wouldn't break the bilingual table but make the result file look closer to the original (for example, if source has italics text, the result would have the same text with italics, etc). At the same time, the result should have unified look.

Requirements:

- Robust code with no special handling of custom classes. The code should work with as many epubs as possible as long as epub is valid.
- Splitter and aligner should work with natural language, because I use state-of-the-art algorithms I don't want to modify.
- Sentences split by newlines in the original files should be pre-split before external splitter run because they are effectively different sentences.
- First sentence of each logical paragraph (logical as in not <p> but the way it's rendered as a reading paragraph) should have indentation. Even if this sentence is first, last, or middle row in the result table.
- I apply aggressive CSS reset to make the result book look unified and to avoid table-breaking markup.
- I don't want to process chapter body in a special way, the HTML/CSS markup from the original files should handle the tags I need (headings/talics/etc) so that chapter looks correct (as in close to what it looked like in the original file), and I only set the looks of some tags in my CSS. So don't extract chapter titles from chapter body or anything like this.
- Unmatched sentences should be displayed. If only one side has unmatched sentences, it should occupy it's row as a regular matched sentence but with one of the columns being empty. If both side have unmatched sentences, they should also be displayed, but you can put them in one row. The order of unmatched sentences should be reading order from original files.
- It's indented to have multiple sentences in a single row if they are aligned to one sentence in another language column that also occupies a row.
- Try not to duplicate code.
- Try to make code as abstract/basic as possible, as in avoiding too specific details that might work with some books but can break with other books. Avoid book-specific code.
- Alignment should work at sentence level, always. Don't try to align entire paragraphs. It MUST be parallel text with sentences aligned side-by-side.
- If you know an existing solution for some problem, you can suggest it.
- Try to simplify code when it's possible to keep the correct logic.
- Before submitting changes, make sure they meet the current requirements.


For testing changes you have two options:

1. Run `bbb` (installed console script; see Gotchas on why not `python -m bbb`) with the arguments from `cli.py` locally. You can either create temporary short epub files and use auto-match option, or use existing epub files (ask user where to find them and get access to that directory) with interactive mode, choosing just a few chapters for testing. Testing chapters shouldn't take more than 2 minutes, so try to reduce the load. `--only extract` / `--only auto-match` show chapters / chapter matching without building the EPUB. The result of the app would be `bilingual.epub` file in the root directory of the project that you should unzip and parse.
2. Or add a new test to existing files in the tests/ directory or create a new file, then recreate the file structure that you want to test. Tests build EPUB fixtures in-memory via helpers in `tests/conftest.py` (`make_epub_bytes`, `create_chapter_html`) and mock the heavy ML deps (SentenceTransformer, SaT, Bertalign) with the `mock_heavy_deps` fixture in `tests/test_integration.py`; use pyfakefs for file I/O.

## Quick reference

- **Python version:** 3.14 (`.python-version`), project requires `>=3.13` (`pyproject.toml`).
- **Setup:** clone with `--recurse-submodules` (bertalign is a submodule), create venv, `pip install -r requirements.txt`. Test everything only inside venv. First real run downloads models (LaBSE, sat-3l).
- **Run tests:** `source .venv/bin/activate && cd /tmp && pytest /path/to/bilingual-book-builder/tests/ -v` (must be run from a cwd *outside* the repo root, see Gotchas).
- **No linter/formatter configured** – follow existing code style.
- **Entry point:** `bbb/cli.py` → `BBB` class orchestrates: extract → map → align → build EPUB.
- **Key files:** `bbb/aligner.py` (bertalign), `bbb/splitter.py` (wtpsplit/sentence-splitter), `bbb/html_tokenizer.py` (HTML → sentences with paragraph-start tracking, HTML-fragment preservation), `bbb/book_builder.py` (EPUB generation with HTML preservation).
- **Output:** `bilingual.epub` in project root (plus a Calibre `bilingual.sdr` sidecar; both gitignored).

## Gotchas

- **Do NOT run `pytest` or `python -m bbb` from the repo root.** The checked-out `bertalign/` submodule dir has no top-level `__init__.py`, so when the repo root is on `sys.path` (i.e. cwd == repo root) Python resolves it as a namespace package that shadows the installed editable `bertalign` package → `ImportError: cannot import name 'Bertalign' from 'bertalign' (unknown location)`. Run tests from any other directory (e.g. `cd /tmp`). The installed `bbb` console script works from anywhere.