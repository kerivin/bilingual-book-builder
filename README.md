# bilingual-book-builder

<img width="300" src="https://github.com/user-attachments/assets/04345fb1-6a57-41f1-bc39-1efc50491af5" />

<img width="300" src="https://github.com/user-attachments/assets/eac6ed36-7e4e-47c0-88fd-ec602c70d869" />


Builds a bilingual ePub from two ePubs of the same book in different languages with sentences aligned side-by-side.

It uses [Bertalign](https://github.com/bfsujason/bertalign), which is proven to be one of the most accurate sentence aligners. Handles many-to-one, many-to-none and one-to-one sentences.

Usage:

`bbb -s original.epub -t translation.epub`

or,

online on [HuggingFace](https://huggingface.co/spaces/cringo/bbb) (blocked in some regions, use proxy/VPN) (also, quite slow)

See [list of supported languages](https://github.com/bfsujason/bertalign#languges-supported)

## Installation

If you have a graphic card:
```
pip install "bbb[gpu] @ git+https://github.com/kerivin/bilingual-book-builder.git"
```

If you don't:
```
pip install "bbb[cpu] @ git+https://github.com/kerivin/bilingual-book-builder.git"
```


<details>
<summary>Development on Linux</summary>

### Prerequisites

- Python (tested on 3.14)
- pip
- git

```
git clone --recurse-submodules https://github.com/kerivin/bilingual-book-builder.git

cd bilingual-book-builder
python -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
python -m bbb --help
```

</details>

## Options

| Command | Description | Example
| --- | --- | --- |
| `-s SOURCE`<br>`--source SOURCE` | Path to source (original) EPUB | `-s original.epub`<br>`--source "original with spaces.epub"` |
| `-t TARGET`<br>`--target TARGET` | Path to target (translation) EPUB | `-t translation.epub`<br>`--target "translation with spaces.epub"` |
| `-sl LANG`<br>`--source-language LANG` | Source (original) language code.<br>Auto-detect if omitted | `-sl en`<br>`--source-language en` |
| `-tl LANG`<br>`--target-language LANG` | Target (translation) language code.<br>Auto-detect if omitted | `-tl ru`<br>`--target-language ru` |
| `-o FILENAME`<br>`--output FILENAME` | New EPUB name.<br>Default: bilingual | `-o book`<br>`--output book` |
| `-m`<br>`--manual` | Match chapters manually in the interactive mode.<br>Default: off if omitted | `-m` |
| `--threads THREADS` | Number of parallel threads.<br>Default: 1 | `--threads 4` |
| `--auto-threshold THRESHOLD` | Similarity threshold value (0.0-1.0) for chapter auto-matching.<br>Default: 0.6 | `--auto-threshold 0.4` |
| `--only {auto-match,extract}` | Only show extracted chapters or auto-matched chapters without generating a new EPUB | `--only auto-match` |
| `--keep-source-chapters` | Keep source (original) chapters with no matching target (translation) chapters.<br>Default: off if omitted | `--keep-source-chapters` |
| `--keep-target-chapters` | Keep target (translation) chapters with no matching source (original) chapters.<br>Default: off if omitted | `--keep-target-chapters` |
| `--model` | Name or path to sentence embedding model<br>Default: [LaBSE](https://huggingface.co/sentence-transformers/LaBSE) | `--model "/home/models/model"` |
| `-v {silent,progress,verbose}`<br>`--verbosity {silent,progress,verbose}` | How verbose logging is.<br>Default: progress | `-v progress`<br>`--verbosity silent` |


Typical usage:

`bbb --help`

`bbb -s original.epub -t translation.epub`

`bbb -s original.epub -t translation.epub -sl en -tl ru --auto-threshold 0.6 --threads 4 --keep-target-chapters`


## Disclaimer

The quality of the new book depends on the quality of the provided books. Some have a weird table of contents or don't have it at all, some duplicate a chapter title at the beginning of the chapter, some have no metadata, etc. Make sure the two books to merge are not broken.

The code is AI-assisted.

## Known issues

- Chapter auto-match is unreliable
- Footnotes, endnotes, bold, italics, etc are lost
- Paragraphs are indistinguishable
- Some readers still display the table borders even thought they are transparent
- Sentence splitter restricts language support since it only support 25 languages, while LaBSE handles ~100
