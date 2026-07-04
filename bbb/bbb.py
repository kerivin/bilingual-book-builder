from fast_ebook import epub
import fast_ebook

class BBB:
    def __init__(self,
                source_epub_path,
                target_epub_path,
                source_language = None,
                target_language = None,
                threads = 1,
                auto_match_chapter_threshold = None,
                only_match_chapters = False,
                keep_unmatched_source_chapters = False,
                keep_unmatched_target_chapters = True
            ):
        self.source_epub_path = source_epub_path
        self.target_epub_path = target_epub_path
        self.source_language = source_language
        self.target_language = target_language
        self.threads = threads
        self.auto_match_chapter_threshold = auto_match_chapter_threshold
        self.only_match_chapters = only_match_chapters
        self.keep_unmatched_source_chapters = keep_unmatched_source_chapters
        self.keep_unmatched_target_chapters = keep_unmatched_target_chapters
    
    def run(self):
        from bbb.chapter_mapper import ChapterMapper
        from bbb.chapter_extractor import ChapterExtractor
        from bbb.chapter_aligner import ChapterAligner

        books = epub.read_epubs([self.source_epub_path, self.target_epub_path], workers=2)
        if not books or len(books) < 2:
            return

        source_chapters = ChapterExtractor(books[0]).get_chapter_list()
        target_chapters = ChapterExtractor(books[1]).get_chapter_list()

        mapper = ChapterMapper(source_chapters, target_chapters, self.keep_unmatched_source_chapters, self.keep_unmatched_target_chapters)
        chapter_pairs = []
        if self.auto_match_chapter_threshold is not None:
            chapter_pairs = mapper.run_auto(threshold=self.auto_match_chapter_threshold)
        else:
            chapter_pairs = mapper.run_interactive()
        
        if self.only_match_chapters or not chapter_pairs:
            return
        
        aligner = ChapterAligner(source_chapters, target_chapters, chapter_pairs, self.source_language, self.target_language, self.threads)
        aligner.run()
        