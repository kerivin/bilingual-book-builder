from pathlib import Path
from typing import Union

from ebooklib import epub
from epub_utils import Document


class EpubFile:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self._document = None
        self._ebook = None
        self._error = None

    def _load(self):
        if self._error is not None or self._document is not None:
            return
        try:
            self._document = Document(str(self.path))
            self._ebook = epub.read_epub(str(self.path))
        except Exception as exc:
            self._error = exc
            self._document = None
            self._ebook = None

    def __bool__(self):
        self._load()
        return self._error is None

    @property
    def document(self) -> Document:
        self._load()
        return self._document

    @property
    def ebook(self) -> epub.EpubBook:
        self._load()
        return self._ebook