import os
import logging
from typing import Literal

from ebooklib import epub

from bbb import progress
from bbb.epub_file import EpubFile
from bbb.extractor import Extractor
from bbb.mapper import Mapper
from bbb.aligner import Aligner
from bbb.book_builder import BookBuilder
from bbb.constants import SRC_FN_PREFIX, TGT_FN_PREFIX

OnlyOption = Literal['extract', 'auto-match']
CoverOption = Literal['source', 'target']

class BBB:
    def __init__(self,
                source_path,
                target_path,
                source_language = None,
                target_language = None,
                output: str = 'bilingual',
                manual: bool = False,
                threads: int = 1,
                auto_threshold: float = 0.6,
                only: OnlyOption | None = None,
                keep_unmatched_source_chapters = False,
                keep_unmatched_target_chapters = False,
                cover: CoverOption = 'source',
                align_model = 'LaBSE',
                split_model = 'sat-3l',
                simple_split: bool = False,
                verbosity: str = 'progress',
                progress_callback = None,
            ):
        self.source_path = source_path
        self.target_path = target_path
        self.source_language = source_language.lower() if source_language else None
        self.target_language = target_language.lower() if target_language else None
        self.output = output
        self.manual = manual
        self.threads = threads
        self.auto_threshold = auto_threshold
        self.only = only
        self.keep_unmatched_source_chapters = keep_unmatched_source_chapters
        self.keep_unmatched_target_chapters = keep_unmatched_target_chapters
        self.cover = cover
        self.align_model = align_model
        self.split_model = split_model
        self.simple_split = simple_split

        progress.init(verbosity, progress_callback)
        self.log = logging.getLogger(__name__)

    def _create_sentence_transformer(self):
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(self.align_model)

    def run(self):
        if not os.path.isfile(self.source_path) or not os.path.isfile(self.target_path):
            self.log.error("Not a file.")
            return

        if os.path.samefile(self.source_path, self.target_path):
            self.log.error("Source and target files are the same.")
            return

        if self.source_language is not None and self.target_language is not None and self.source_language == self.target_language:
            self.log.error("Source and target languages are the same.")
            return

        source_book = EpubFile(self.source_path)
        if not source_book:
            self.log.error(f"Failed to read source EPUB file {self.source_path}.")
            return

        target_book = EpubFile(self.target_path)
        if not target_book:
            self.log.error(f"Failed to read target EPUB file {self.target_path}.")
            return None

        source_extractor = Extractor(
            epub_file = source_book,
            force_show = self.only == 'extract',
            fn_prefix = SRC_FN_PREFIX,
        )
        source_chapters, source_footnotes = source_extractor.get_chapter_list()

        target_extractor = Extractor(
            epub_file = target_book,
            force_show = self.only == 'extract',
            fn_prefix = TGT_FN_PREFIX,
        )
        target_chapters, target_footnotes = target_extractor.get_chapter_list()

        if not source_chapters or not target_chapters:
            self.log.error("No chapters extracted from one or both books.")
            return

        if self.only == 'extract':
            return

        mapper = Mapper(
            source_chapters = source_chapters,
            target_chapters = target_chapters,
            keep_unmatched_source_chapters = self.keep_unmatched_source_chapters,
            keep_unmatched_target_chapters = self.keep_unmatched_target_chapters,
        )

        sentence_transformer = None
        chapter_pairs = []
        if not self.manual:
            sentence_transformer = self._create_sentence_transformer()
            chapter_pairs = mapper.run_auto(
                model = sentence_transformer,
                force_show = progress.get_verbosity() == 'verbose' or self.only == 'auto-match',
                threshold = self.auto_threshold,
            )
        else:
            chapter_pairs = mapper.run_interactive()

        if not chapter_pairs:
            self.log.error("No chapters to align")
            return

        if self.only == 'auto-match':
            return

        if sentence_transformer is None:
            sentence_transformer = self._create_sentence_transformer()

        aligned = Aligner(
            source_chapters,
            target_chapters,
            chapter_pairs,
            self.source_language,
            self.target_language,
            self.threads,
            sentence_transformer,
            self.split_model if not self.simple_split else None,
        ).run()

        if not aligned:
            self.log.error("No aligned chapters produced.")
            return

        new_book = BookBuilder(
            source_book = source_book,
            target_book = target_book,
            blocks = aligned,
            copy_target_cover = self.cover == 'target',
            source_footnotes = source_footnotes,
            target_footnotes = target_footnotes,
        ).run()

        if not new_book:
            self.log.error("Failed to build the new book.")
            return

        if not self.output.lower().endswith(".epub"):
            self.output += ".epub"
        epub.write_epub(self.output, new_book)
        self.log.info("EPUB written successfully.")