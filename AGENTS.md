Builds a parallel-text EPUB with sentences aligned side-by-side from two EPUBs of the same book (original + translation). Preserves some original HTML formatting (italics, bold, headings) but applies an aggressive CSS reset so the result looks unified and the original markup can't break the bilingual table. Don't write comments in the code; explain your solution outside it.

## Design constraints (do not violate)

- No book-specific code or special-casing of custom classes; must work on any valid EPUB.
- Alignment is always at sentence level — never align whole paragraphs.
- Sentences split by newlines in the original must be pre-split before the external splitter runs: they are effectively different sentences.
- The first sentence of each logical paragraph (rendered reading paragraph, not `<p>`) gets indentation, regardless of whether it lands as the first, last, or middle row of the table.
- Don't special-case chapter structure (e.g. don't extract chapter titles from the chapter body); let the original HTML/CSS handle headings, italics, etc.
- Unmatched sentences are still displayed: one-sided unmatches get a regular row with one empty column; both-sided unmatches share one row; keep reading order from the original files.
- Multiple sentences may share a row when they align to a single sentence on the other side.

## Commands

- **Setup:** clone with `--recurse-submodules` (bertalign is a submodule); `python -m venv .venv`; `source .venv/bin/activate`; `pip install -r requirements.txt`. That installs `-e ./bertalign[gpu]` — change to `[cpu]` in `requirements.txt` if you have no GPU. Python `>=3.13` (`.python-version` = 3.14). First real run downloads models (LaBSE, sat-3l).
- **Run tests:** `source .venv/bin/activate && cd /tmp && pytest /path/to/bilingual-book-builder/tests/ -v`. MUST run from a cwd *outside* the repo root (see Gotchas). No linter/formatter configured — follow existing code style.
- **Run the app:** use the installed `bbb` console script (`python -m bbb` fails from the repo root). `--only extract` / `--only auto-match` short-circuit before building the EPUB — use them to test extraction/chapter-matching. Full run writes `bilingual.epub` to project root (gitignored). For real books, ask the user where the EPUBs live; test only a few chapters (keep it under ~2 minutes).
- Inspect a book's TOC (when you need to compare with the extractor output): `epub-utils "path.epub" toc` (installed as a dependency).

## Architecture

Entry point `bbb/cli.py` → `BBB` (`bbb/bbb.py`) orchestrates: extract → map → align → build EPUB.

- `bbb/extractor.py` — chapters + footnotes from an EPUB.
- `bbb/mapper.py` — chapter matching (auto via SentenceTransformer + threshold, or interactive `-m`).
- `bbb/splitter.py` — sentence splitting: wtpsplit `SaT` (default) or `sentence_splitter.SentenceSplitter` with `--simple-split`.
- `bbb/aligner.py` — bertalign alignment at sentence level.
- `bbb/html_tokenizer.py` — HTML → sentences with paragraph-start tracking and HTML-fragment preservation.
- `bbb/book_builder.py` — EPUB generation with HTML preservation.

## Testing

Unit tests build EPUB fixtures in-memory via helpers in `tests/conftest.py` (`make_epub_bytes`, `create_chapter_html`) and use pyfakefs (`fs` fixture) for file I/O. The `mock_heavy_deps` fixture in `tests/test_integration.py` mocks the ML deps (SentenceTransformer, SaT, SentenceSplitter, Bertalign) so integration tests don't download models; tests pass `simple_split=True`.

## Gotchas

- **Do NOT run `pytest` or `python -m bbb` from the repo root.** The checked-out `bertalign/` submodule dir has no top-level `__init__.py`, so when the repo root is on `sys.path` (i.e. cwd == repo root) Python resolves it as a namespace package that shadows the installed editable `bertalign` package → `ImportError: cannot import name 'Bertalign' from 'bertalign' (unknown location)`. Run tests from any other directory (e.g. `cd /tmp`). The installed `bbb` console script works from anywhere.
