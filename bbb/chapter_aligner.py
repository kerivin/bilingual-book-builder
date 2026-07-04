from bertalign.bertalign import Bertalign
from typing import List, Dict, Any
from enum import Enum

class FilterMode(Enum):
    HEADING = 0
    NOT_NUMBER = 1

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

    def _align_pair(self, source_text: str, target_text: str) -> List[Dict[str, str]]:
        if not source_text.strip() or not target_text.strip():
            return []

        try:
            model = Bertalign(
                source_text, target_text,
                self.source_language, self.target_language
            )
            model.align_sents()
            # model.print_sents()
            src_sents = model.src_sents
            tgt_sents = model.tgt_sents
            aligned = []
            for align in model.result:
                # bertalign returns a tuple: (src_indices, tgt_indices)
                src_indices, tgt_indices = align
                src_seg = ' '.join(model.src_sents[i] for i in src_indices)
                tgt_seg = ' '.join(model.tgt_sents[i] for i in tgt_indices)
                aligned.append({'source': src_seg, 'target': tgt_seg})
            return aligned
        except Exception as e:
            print(f"Bertalign error: {e}")
            return [{'source': source_text, 'target': target_text}]

    def run(self) -> List[Dict[str, Any]]:
        """
        Returns a list of chapter blocks in the exact order of self.chapter_order.
        Each block:
          {
            'source': { 'title', 'text', 'index' } or None,
            'target': { 'title', 'text', 'index' } or None,
            'alignment': [{'source': str, 'target': str}, ...] or None
          }
        """
        output = []
        for source_index, target_index in self.chapter_pairs:
            
            block = {'source': None, 'target': None, 'alignment': None}

            if source_index is not None:
                ch = self.source_chapters[source_index]
                block['source'] = {
                    'title': ch['title'],
                    'text': ch['full_text'] if target_index is None else None,
                    'index': ch['index']
                }
                
            if target_index is not None:
                ch = self.target_chapters[target_index]
                block['target'] = {
                    'title': ch['title'],
                    'text': ch['full_text'] if source_index is None else None,
                    'index': ch['index']
                }
                
            if source_index is not None and target_index is not None:
                src_text = self.source_chapters[source_index]['full_text']
                tgt_text = self.target_chapters[target_index]['full_text']
                block['alignment'] = self._align_pair(src_text, tgt_text)

            output.append(block)

        return output