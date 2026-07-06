from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from bertalign.encoder import Encoder

class ChapterAligner:
    def __init__(
        self,
        source_chapters: List[Dict[str, Any]],
        target_chapters: List[Dict[str, Any]],
        chapter_pairs,
        source_language,
        target_language,
        threads: int,
        model
    ):
        self.source_chapters = source_chapters
        self.target_chapters = target_chapters
        self.chapter_pairs = chapter_pairs
        self.source_language = source_language
        self.target_language = target_language
        self.threads = threads
        self.model_encoder = Encoder(model)

    def _align_pair(self, source_text: str, target_text: str) -> List[Dict[str, str]]:
        if not source_text.strip() or not target_text.strip():
            return []

        from bertalign.bertalign import Bertalign
        try:
            aligner = Bertalign(
                self.model_encoder,
                source_text, target_text,
                self.source_language, self.target_language
            )
            aligner.align_sents()
            # model.print_sents()
            aligned = []
            for align in aligner.result:
                # bertalign returns a tuple: (src_indices, tgt_indices)
                src_indices, tgt_indices = align
                src_seg = ' '.join(aligner.src_sents[i] for i in src_indices)
                tgt_seg = ' '.join(aligner.tgt_sents[i] for i in tgt_indices)
                aligned.append({'source': src_seg, 'target': tgt_seg})
            return aligned
        except Exception as e:
            print(f"Bertalign error: {e}")
            return [{'source': source_text, 'target': target_text}]

    def run(self) -> List[Dict[str, Any]]:
        """
        Returns a list of chapter blocks in the exact order of self.chapter_pairs.
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

            output.append(block)

        pairs_to_align = []
        for idx, (src_idx, tgt_idx) in enumerate(self.chapter_pairs):
            if src_idx is not None and tgt_idx is not None:
                src_text = self.source_chapters[src_idx]['full_text']
                tgt_text = self.target_chapters[tgt_idx]['full_text']
                pairs_to_align.append((idx, src_text, tgt_text))

        if self.threads > 1 and pairs_to_align:
            with ThreadPoolExecutor(max_workers=self.threads) as executor:
                future_to_idx = {
                    executor.submit(self._align_pair, src, tgt): (idx, src, tgt)
                    for idx, src, tgt in pairs_to_align
                }
                for future in as_completed(future_to_idx):
                    idx, src_text, tgt_text = future_to_idx[future]
                    try:
                        alignment = future.result()
                    except Exception as e:
                        print(f"Error aligning chapter pair {idx}: {e}")
                        alignment = [{'source': src_text, 'target': tgt_text}]
                    output[idx]['alignment'] = alignment
        else:
            for idx, src_text, tgt_text in pairs_to_align:
                output[idx]['alignment'] = self._align_pair(src_text, tgt_text)

        return output