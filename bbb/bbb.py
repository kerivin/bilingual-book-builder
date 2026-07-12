from ebooklib import epub
import logging
from typing import Literal

from bbb import progress
from bbb.chapter_extractor import ChapterExtractor
from bbb.chapter_mapper import ChapterMapper
from bbb.chapter_aligner import ChapterAligner
from bbb.book_builder import BookBuilder

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
                only: str | None = None,
                keep_unmatched_source_chapters = False,
                keep_unmatched_target_chapters = False,
                cover: Literal['source', 'target'] = 'source',
                align_model = 'LaBSE',
                split_model = 'sat-3l',
                simple_split: bool = False,
                verbosity: str = 'progress',
                progress_callback = None,
            ):
        self.source_path = source_path
        self.target_path = target_path
        self.source_language = source_language
        self.target_language = target_language
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
        source_chapters = ChapterExtractor(
            path = self.source_path,
            force_show = self.only == 'extract'
        ).get_chapter_list()

        target_chapters = ChapterExtractor(
            path = self.target_path,
            force_show = self.only == 'extract'
        ).get_chapter_list()

        if not source_chapters or not target_chapters:
            self.log.error("No chapters extracted from one or both books.")
            return

        if self.only == 'extract':
            return

        mapper = ChapterMapper(
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

        aligned_chapters = ChapterAligner(
            source_chapters,
            target_chapters,
            chapter_pairs,
            self.source_language,
            self.target_language,
            self.threads,
            sentence_transformer,
            self.split_model if not self.simple_split else None,
        ).run()

        if not aligned_chapters:
            self.log.error("No aligned chapters produced.")
            return
        
        new_book = BookBuilder(
            source_path = self.source_path,
            target_path = self.target_path,
            blocks = aligned_chapters,
            copy_target_cover = self.cover == 'target'
        ).run()

        if not new_book:
            self.log.error("Failed to build the new book.")
            return
        
        if not self.output.lower().endswith(".epub"):
            self.output += ".epub"
        epub.write_epub(self.output, new_book)
        self.log.info("EPUB written successfully.")
        