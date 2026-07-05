# bilingual-book-builder

<img width="300" align="left" alt="image_2026-07-05_13-02-23" src="https://github.com/user-attachments/assets/04345fb1-6a57-41f1-bc39-1efc50491af5" />

Builds a bilingual ePub from two ePubs of the same book in different languages using [Bertalign](https://github.com/bfsujason/bertalign), which is proven to be one of the most accurate sentence aligners. Handles many-to-one, many-to-none and one-to-one sentences.

Usage: `python -m bbb -s original.epub -t translation.epub`

<br clear="left"/>

### Prerequisites

- Python (tested on 3.14)
- pip
- git


### Installation
```
git clone https://github.com/kerivin/bilingual-book-builder.git
cd bilingual-book-builder
git submodule update --init --recursive

pip install --upgrade pip
pip install -r requirements.txt
python -m bbb --help
```

### Options

| Command | Description | Example
| --- | --- | --- |
| `-s SOURCE`<br>`--source SOURCE` | Path to source (original) ePub | `-s original.epub`<br>`--source "original with spaces.epub"` |
| `-t TARGET`<br>`--target TARGET` | Path to target (translation) ePub | `-t translation.epub`<br>`--target "translation with spaces.epub"` |
| `-sl SOURCE_LANGUAGE`<br>`--source-language SOURCE_LANGUAGE` | Source (original) language code.<br>Auto-detect if omitted | `-sl en`<br>`--source-language en` |
| `-tl TARGET_LANGUAGE`<br>`--target-language TARGET_LANGUAGE` | Target (translation) language code.<br>Auto-detect if omitted | `-tl ru`<br>`--target-language ru` |
| `-o FILENAME`<br>`--output FILENAME` | New ePub name.<br>Default: bilingual.epub | `-o book.epub`<br>`--output book.epub` |
| `--threads THREADS` | Number of processing threads.<br>Default: 1 | `--threads 4` |
| `--auto-match-chapters [THRESHOLD]` | Auto-match chapters instead of manual matching.<br>Default: off.<br>Not recommended to use. | `--auto-match-chapters`<br>`--auto-match-chapters 0.6` |
| `--only-match-chapters` | Exit after auto-match chapters.<br>Requires `--auto-match-chapters` option.<br>Useful for checking what auto-match would look like without creating a ePub | `--only-match-chapters` |
| `--keep-source-chapters` | Keep source (original) chapters with no matching target (translation) chapters.<br>Default: off if omitted | `--keep-source-chapters` |
| `--keep-target-chapters` | Keep target (translation) chapters with no matching source (original) chapters.<br>Default: off if omitted | `--keep-target-chapters` |


Typical usage:

`python -m bbb --help`

`python -m bbb -s original.epub -t translation.epub`

`python -m bbb -s original.epub -t translation.epub -sl en -tl ru --threads 4 --keep-target-chapters`

### Disclaimer

The quality of the new book depends on the quality of the provided books. Some have a weird table of contents or don't have it at all, some duplicate a chapter title at the beginning of the chapter, some have no metadata, etc. Make sure the two books to merge are not broken.
