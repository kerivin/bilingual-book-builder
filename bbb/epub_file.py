from pathlib import Path
from typing import Union

from ebooklib import epub
from epub_utils import Document


class EpubFile:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self._document = None
        self._ebook = None

    @property
    def document(self) -> Document:
        if self._document is None:
            self._document = Document(str(self.path))
        return self._document

    @property
    def ebook(self) -> epub.EpubBook:
        if self._ebook is None:
            self._ebook = epub.read_epub(str(self.path))
        return self._ebook

    def get_metadata(self, namespace, key):
        return self.ebook.get_metadata(namespace, key)

    @property
    def spine(self):
        return self.ebook.spine

    @property
    def toc(self):
        return self.ebook.toc

    @property
    def guide(self):
        return getattr(self.ebook, 'guide', [])

    def get_item_with_href(self, href):
        return self.ebook.get_item_with_href(href)

    def get_item_with_id(self, item_id):
        return self.ebook.get_item_with_id(item_id)