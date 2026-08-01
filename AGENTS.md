Builds a parallel-text EPUB with sentences aligned side-by-side from two EPUBs of the same book (original + translation). Preserves some original HTML formatting (italics, bold, headings) but applies an aggressive CSS reset so the result looks unified and the original markup can't break the bilingual table. Don't write comments in the code; explain your solution outside it.

## Design constraints (do not violate)

- No book-specific code or special-casing of custom classes; must work on any valid EPUB.
- Never hardcode any language-specific details. It must be language-agnostic.
- Alignment is always at sentence level — never align whole paragraphs.
- Sentences split by newlines in the original must be pre-split before the external splitter runs: they are effectively different sentences.
- The first sentence of each logical paragraph (rendered reading paragraph, not `<p>`) gets indentation, regardless of whether it lands as the first, last, or middle row of the table.
- Don't special-case chapter structure (e.g. don't extract chapter titles from the chapter body); let the original HTML/CSS handle headings, italics, etc.
- Unmatched sentences are still displayed: one-sided unmatches get a regular row with one empty column; both-sided unmatches share one row; keep reading order from the original files.
- Multiple sentences may share a row when they align to a single sentence on the other side.

## Commands

- **Setup:** clone with `--recurse-submodules` (bertalign is a submodule — the `kerivin/bertalign` fork, not upstream); `python -m venv .venv`; `source .venv/bin/activate`; `pip install -r requirements.txt`. That installs `-e ./bertalign[gpu]` — change to `[cpu]` in `requirements.txt` if you have no GPU. Python `>=3.13` (`.python-version` = 3.14). First real run downloads models (LaBSE, sat-3l).
- **Run tests:** `source .venv/bin/activate && pytest tests/ -v`. Can be run from the repo root — the `bertalign/` submodule carries a compatibility shim (`bertalign/__init__.py`) so it resolves correctly even when the repo root is on `sys.path`. No linter/formatter configured — follow existing code style. CI (`.github/workflows/test.yml`) runs the same suite on push/PR for Python 3.13 & 3.14. Single test: `pytest tests/test_book_builder.py::test_styles_not_copied`.
- **Run the app:** use the installed `bbb` console script, or `python -m bbb` — both work from any directory, including the repo root. Pass `-sl`/`-tl` to skip lingua auto-detection and improve matching; `--threads N` parallelizes alignment (default 1). `--only extract` / `--only auto-match` short-circuit before building the EPUB — use them to test extraction/chapter-matching. `--only auto-match` cannot be combined with `-m` (it errors). Full run writes `bilingual.epub` to project root (gitignored). For real books, ask the user where the EPUBs live.
- **Quick test on a real book:** a full auto-build aligns every chapter and takes minutes (~8-10 min for some books). To verify a real book fast, build only a few pairs with manual mapping: `bbb -s book.epub -t book_tr.epub -sl en -tl ru -m -o out`. In the interactive prompt: `[ENTER]` matches the shown pair, `S` skips the source chapter, `T` skips the target chapter, `B` goes back, `F` skips the rest and finishes, `Q` quits. After the mapping round it asks `[Y]es / [N]o (redo) / [Q]uit` to confirm the whole mapping. For testing you would use 'F' so that you don't process the entire books but only a few chapters. Unmatched chapters are dropped unless `--keep-source-chapters` / `--keep-target-chapters`. Keep such a test under ~2 minutes of alignment.
- Inspect a book's TOC (when you need to compare with the extractor output): `epub-utils "path.epub" toc` (installed as a dependency).

## Architecture

Entry point `bbb/cli.py` → `BBB` (`bbb/bbb.py`) orchestrates: extract → map → align → build EPUB. `bbb/__main__.py` is only used by `python -m bbb` and the PyInstaller build (`.github/workflows/build.yml`, manual dispatch).

- `bbb/extractor.py` — chapters + footnotes from an EPUB.
- `bbb/mapper.py` — chapter matching (auto via SentenceTransformer + threshold, or interactive `-m`). Auto-match signatures are content-based: title + first/last ~1000 chars of text built from the chapter's `content_html`.
- `bbb/splitter.py` — sentence splitting: wtpsplit `SaT` (default) or `sentence_splitter.SentenceSplitter` with `--simple-split`.
- `bbb/aligner.py` — bertalign alignment at sentence level.
- `bbb/html_tokenizer.py` — HTML → sentences with paragraph-start tracking and HTML-fragment preservation.
- `bbb/book_builder.py` — EPUB generation with HTML preservation.

## Testing

Unit tests build EPUB fixtures in-memory via helpers in `tests/conftest.py` (`make_epub_bytes`, `create_chapter_html`) and use pyfakefs (`fs` fixture) for file I/O. The `mock_heavy_deps` fixture in `tests/test_integration.py` mocks the ML deps (SentenceTransformer, SaT, SentenceSplitter, Bertalign) so integration tests don't download models; tests pass `simple_split=True`.

## Gotchas

- **Never copy or `<link>` the source/target books' CSS into the output.** Only the built-in `generic.css` (the aggressive reset) is linked. Original stylesheets can hide translated text — calibre EPUBs ship `display: none` rules (e.g. `.calibre1` for screen-reader duplicates) that make the bilingual column silently empty — or break the table. A `_copy_styles` helper that did this was removed; don't reintroduce it.
- The extractor's chapter dicts have **no `full_text` key**. The mapper builds auto-match signatures from `content_html` text; if it falls back to title-only the auto-match collapses to a handful of pairs. Don't "simplify" the signature lookup.
- The `bertalign/` submodule is checked out at `bertalign/`, which normally would shadow the installed `bertalign` package when the repo root is on `sys.path` (cwd == repo root). The submodule root carries a shim `__init__.py` that re-exports the inner package and aliases its submodules, so `pytest` / `python -m bbb` work from the repo root. Keep the shim and the relative imports inside `bertalign/bertalign/*` in sync if you update the submodule.
