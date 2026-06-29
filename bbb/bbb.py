class BBB:
    def __init__(self,
                source_epub_path,
                target_epub_path,
                source_language = None,
                target_language = None,
                threads = 1,
                auto_accept_mapping = False
            ):
        self.source_epub_path = source_epub_path
        self.target_epub_path = target_epub_path
        self.source_language = source_language
        self.target_language = target_language
        self.threads = threads
        self.auto_accept_mapping = auto_accept_mapping
    
    def run(self):
        # from bertalign.bertalign import Bertalign
        from bbb.chapter_mapper import ChapterMapper
        from bbb.chapter_extractor import ChapterExtractor

        src_chapters = ChapterExtractor(self.source_epub_path).get_chapter_list()
        tgt_chapters = ChapterExtractor(self.target_epub_path).get_chapter_list()

        mapper = ChapterMapper(src_chapters, tgt_chapters)
        chapter_pairs = mapper.run()