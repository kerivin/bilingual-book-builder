# bilingual-book-builder

<img width="300" alt="Screenshot" src="https://github.com/user-attachments/assets/4431d770-a0c6-4af6-ba49-cf1a7e1a0565" />

Builds a parallel text ePub with sentences aligned side-by-side from two ePubs of the same book in different languages.

#### Usage:

`bbb -s original.epub -t translation.epub`

or,

online on [HuggingFace](https://huggingface.co/spaces/cringo/bbb) (blocked in some regions, use proxy/VPN) (also, quite slow)

## Supported Languages

Languages restricted to that of the libraries and models used. Basically, there are 3 stages, each supporting their own set of languages. I tried to make bbb support as many languages as possible, so it mostly depends on configurable models.

1. Language detector: lingua supports [75 languages](https://github.com/pemistahl/lingua-py#4-which-languages-are-supported) (but you can specify languages manually to skip this)
2. Sentence splitter: the simple one (`--simple-split`) supports [24 languages](https://github.com/mediacloud/sentence-splitter/tree/develop#languages); Wtpsplit depends on a model (`--split-model`) and currently supports [85 languages](https://arxiv.org/html/2406.16678v2#A1.T15). Simple splitter is enough for regular books in some European language.
3. Sentence alignment: bertalign depends on a model (`--align-model`) and currently supports [109 languages](https://huggingface.co/sentence-transformers/LaBSE).

You can just give it a try and see if it works with your languages. Chances are it would even if some of the modules don't support them. That's how it's supposed to work anyway.

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

| Command | Description
| --- | --- |
| `-s SOURCE`<br>`--source SOURCE` | Path to source (original) EPUB
| `-t TARGET`<br>`--target TARGET` | Path to target (translation) EPUB
| `-sl LANG`<br>`--source-language LANG` | Source (original) language code.<br>Auto-detect if omitted
| `-tl LANG`<br>`--target-language LANG` | Target (translation) language code.<br>Auto-detect if omitted
| `-o FILENAME`<br>`--output FILENAME` | New EPUB name.<br>Default: bilingual
| `-m`<br>`--manual` | Match chapters manually in the interactive mode.<br>Default: off
| `--threads THREADS` | Number of parallel threads.<br>Default: 1
| `--auto-threshold THRESHOLD` | Similarity threshold value (0.0-1.0) for chapter auto-matching.<br>Default: 0.6
| `--only {auto-match,extract}` | Only show extracted chapters or auto-matched chapters without generating a new EPUB
| `--keep-source-chapters` | Keep source (original) chapters with no matching target (translation) chapters.<br>Default: off
| `--keep-target-chapters` | Keep target (translation) chapters with no matching source (original) chapters.<br>Default: off
| `--cover {source, target}` | Which book's cover to copy for the new EPUB.<br>Default: source
| `--align-model` | Name or path to sentence embedding model.<br>Default: [LaBSE](https://huggingface.co/sentence-transformers/LaBSE)
| `--split-model` | Name or path to text-to-sentences splitter model.<br>Default: [sat-3l](https://huggingface.co/segment-any-text/sat-3l)
| `--simple-split` | Use simple sentence splitter (not the `--split-model` one) which works faster but with [fewer languages](https://github.com/mediacloud/sentence-splitter/tree/develop#languages).<br>Default: off
| `-v {silent,progress,verbose}`<br>`--verbosity {silent,progress,verbose}` | How verbose logging is.<br>Default: progress


#### Typical usage

`bbb --help`

`bbb -s original.epub -t translation.epub`

`bbb -s original.epub -t translation.epub -sl en -tl ru --simple-split --auto-threshold 0.6 --threads 4 --keep-target-chapters`

#### Options for maximum performance

1. Specify languages with `-sl` and `-tl`
2. Set `--threads`
3. Use `--simple-split` (if simple-split [supports your languages](https://github.com/mediacloud/sentence-splitter/tree/develop#languages))

#### Options for maximum accuracy

1. Specify languages with `-sl` and `-tl`
2. Don't use `--simple-split`
3. Optionally, you can use advanced aligner and splitter models. For now, they are [LaBSE](https://huggingface.co/sentence-transformers/LaBSE) and [sat-3l](https://huggingface.co/segment-any-text/sat-3l), but you can find models that work better with your languages and specify them with `--align-model` and `--split-model`
4. If chapters were mapped incorrectly, map them manually with `-m` or keep auto-matching with a different `--auto-threshold`. To see how the similarity threshold affects the chapters mapping, use `--only auto-match`

## Disclaimer

The quality of the new book depends on the quality of the provided books. Some have a weird table of contents or don't have it at all, some duplicate a chapter title at the beginning of the chapter, some have no metadata, etc. Make sure the two books to merge are not broken.

The code is AI-assisted.

## Known issues

- Bold, italics, etc styles are lost
- Some readers still display the table borders even thought they are transparent
- Should I clean titles not tagged as titles leaking into the chapter body?
- Now I only build side-dy-side horizontal alignment, maybe I should add translation-after-original vertical alignment
