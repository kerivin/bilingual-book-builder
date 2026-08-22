import os
import logging
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from ebooklib import epub

from bbb.epub_file import EpubFile
from bbb.constants import SRC_FN_PREFIX, TGT_FN_PREFIX
from bbb.progress import ProgressReporter

OnlyOption = Literal['extract', 'auto-match']
CoverOption = Literal['source', 'target']


@dataclass
class Config:
    source_path: str
    target_path: str
    source_language: Optional[str] = None
    target_language: Optional[str] = None
    output: str = 'bilingual'
    manual: bool = False
    threads: int = 1
    auto_threshold: float = 0.6
    only: Optional[OnlyOption] = None
    keep_unmatched_source_chapters: bool = False
    keep_unmatched_target_chapters: bool = False
    cover: CoverOption = 'source'
    align_model: str = 'LaBSE'
    split_model: str = 'sat-3l'
    simple_split: bool = False
    verbosity: str = 'progress'
    progress_callback: Optional[Callable] = None

    def __post_init__(self):
        if self.source_language:
            self.source_language = self.source_language.lower()
        if self.target_language:
            self.target_language = self.target_language.lower()


class BBB:
    def __init__(self, config: Config):
        self.config = config
        self.progress = ProgressReporter(config.verbosity, config.progress_callback)
        self.log = logging.getLogger(__name__)

    def _create_sentence_transformer(self):
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(self.config.align_model)

    def _validate_inputs(self) -> bool:
        if not os.path.isfile(self.config.source_path) or not os.path.isfile(self.config.target_path):
            self.log.error("Not a file.")
            return False

        if os.path.samefile(self.config.source_path, self.config.target_path):
            self.log.error("Source and target files are the same.")
            return False

        if (self.config.source_language is not None
                and self.config.target_language is not None
                and self.config.source_language == self.config.target_language):
            self.log.error("Source and target languages are the same.")
            return False
        return True

    def _load_books(self):
        source_book = EpubFile(self.config.source_path)
        if not source_book:
            self.log.error(f"Failed to read source EPUB file {self.config.source_path}.")
            return None
        target_book = EpubFile(self.config.target_path)
        if not target_book:
            self.log.error(f"Failed to read target EPUB file {self.config.target_path}.")
            return None
        return source_book, target_book

    def _map(self, mapper, sentence_transformer):
        if not self.config.manual:
            chapter_pairs = mapper.run_auto(
                model=sentence_transformer,
                force_show=self.config.verbosity == 'verbose' or self.config.only == 'auto-match',
                threshold=self.config.auto_threshold,
            )
        else:
            chapter_pairs = mapper.run_interactive()
        return chapter_pairs

    def run(self) -> bool:
        if not self._validate_inputs():
            return False
        books = self._load_books()
        if books is None:
            return False
        source_book, target_book = books

        from bbb.extractor import Extractor

        force_show = self.config.only == 'extract'
        source_chapters, source_footnotes = Extractor(
            epub_file=source_book,
            force_show=force_show,
            fn_prefix=SRC_FN_PREFIX,
        ).get_chapter_list()
        target_chapters, target_footnotes = Extractor(
            epub_file=target_book,
            force_show=force_show,
            fn_prefix=TGT_FN_PREFIX,
        ).get_chapter_list()
        if not source_chapters or not target_chapters:
            self.log.error("No chapters extracted from one or both books.")
            return False

        if self.config.only == 'extract':
            return True

        from bbb.mapper import Mapper

        mapper = Mapper(
            source_chapters=source_chapters,
            target_chapters=target_chapters,
            keep_unmatched_source_chapters=self.config.keep_unmatched_source_chapters,
            keep_unmatched_target_chapters=self.config.keep_unmatched_target_chapters,
        )

        from bbb.aligner import Aligner

        sentence_transformer = self._create_sentence_transformer()
        chapter_pairs = self._map(mapper, sentence_transformer)
        if not chapter_pairs:
            self.log.error("No chapters to align")
            return False

        if self.config.only == 'auto-match':
            return True

        from bbb.book_builder import BookBuilder

        aligned = Aligner(
            source_chapters,
            target_chapters,
            chapter_pairs,
            self.config.source_language,
            self.config.target_language,
            self.config.threads,
            sentence_transformer,
            self.config.split_model if not self.config.simple_split else None,
            progress_reporter=self.progress,
        ).run()

        if not aligned:
            self.log.error("No aligned chapters produced.")
            return False

        new_book = BookBuilder(
            source_book=source_book,
            target_book=target_book,
            blocks=aligned,
            copy_target_cover=self.config.cover == 'target',
            source_footnotes=source_footnotes,
            target_footnotes=target_footnotes,
            progress_reporter=self.progress,
        ).run()

        if not new_book:
            self.log.error("Failed to build the new book.")
            return False

        output = self.config.output
        if not output.lower().endswith(".epub"):
            output += ".epub"
        try:
            epub.write_epub(output, new_book)
        except Exception as e:
            self.log.error(f"Failed to write EPUB file {output}: {e}")
            return False
        self.log.info("EPUB written successfully.")
        return True