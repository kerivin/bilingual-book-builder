from ebooklib import epub
import logging
from bbb import progress
from bbb.chapter_extractor import ChapterExtractor
from bbb.chapter_mapper import ChapterMapper
from bbb.chapter_aligner import ChapterAligner
from bbb.book_builder import BookBuilder

class BBB:
    def __init__(self,
                source_epub_path,
                target_epub_path,
                source_language = None,
                target_language = None,
                output: str = 'bilingual',
                threads = 1,
                auto_match_chapter_threshold = None,
                only: str | None = None,
                keep_unmatched_source_chapters = False,
                keep_unmatched_target_chapters = False,
                model = 'LaBSE',
                verbosity: str = 'progress',
                progress_callback = None
            ):
        self.source_epub_path = source_epub_path
        self.target_epub_path = target_epub_path
        self.source_language = source_language
        self.target_language = target_language
        self._check_languages()
        self.output = output
        self.threads = threads
        self.auto_match_chapter_threshold = auto_match_chapter_threshold
        self.only = only
        self.keep_unmatched_source_chapters = keep_unmatched_source_chapters
        self.keep_unmatched_target_chapters = keep_unmatched_target_chapters
        self.model = model

        progress.init(verbosity, progress_callback)
        self.log = logging.getLogger(__name__)
    
    def _create_sentence_transformer(self):
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(self.model)

    def _check_languages(self):
        from bertalign.utils import check_language

        if self.source_language is not None:
            self.source_language = self.source_language.lower()
            check_language(self.source_language)
        
        if self.target_language is not None:
            self.target_language = self.target_language.lower()
            check_language(self.target_language)

    def run(self):
        source_chapters = ChapterExtractor(
            path = self.source_epub_path,
            force_show = self.only == 'extract'
        ).get_chapter_list()

        target_chapters = ChapterExtractor(
            path = self.target_epub_path,
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
            keep_unmatched_target_chapters = self.keep_unmatched_target_chapters
        )
        
        sentence_transformer = None
        chapter_pairs = []
        if self.auto_match_chapter_threshold is not None:
            sentence_transformer = self._create_sentence_transformer()
            chapter_pairs = mapper.run_auto(
                model = sentence_transformer,
                force_show = progress.get_verbosity() == 'verbose' or self.only == 'auto-match',
                threshold = self.auto_match_chapter_threshold
            )
        else:
            chapter_pairs = mapper.run_interactive()
        
        if self.only == 'auto-match' or not chapter_pairs:
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
            sentence_transformer
        ).run()

        if not aligned_chapters:
            self.log.error("No aligned chapters produced.")
            return
        
        new_book = BookBuilder(
            source_path = self.source_epub_path,
            target_path = self.target_epub_path,
            blocks = aligned_chapters
        ).run()
        
        if not new_book:
            self.log.error("Failed to build the new book.")
            return
        
        if not self.output.lower().endswith(".epub"):
            self.output += ".epub"
        epub.write_epub(self.output, new_book)
        self.log.info("EPUB written successfully.")
        