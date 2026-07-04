from bertalign.bertalign import Bertalign
from typing import List, Dict, Any

class ChapterAligner:
    def __init__(
        self,
        source_chapters: List[Dict[str, Any]],
        target_chapters: List[Dict[str, Any]],
        chapter_pairs,
        source_language,
        target_language,
        threads: int
    ):
        self.source_chapters = source_chapters
        self.target_chapters = target_chapters
        self.chapter_pairs = chapter_pairs
        self.source_language = source_language
        self.target_language = target_language
        self.threads = threads

    def run(self):
        pass