from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from itertools import accumulate
from bisect import bisect_right

from bbb import progress
from bbb.chapter_splitter import ChapterSplitter
from lingua import LanguageDetectorBuilder, LanguageDetector, Language, IsoCode639_1
from sentence_transformers import SentenceTransformer
from bertalign import Bertalign
from bertalign.encoder import Encoder

class ChapterAligner:
    def __init__(
        self,
        source_chapters: List[Dict[str, Any]],
        target_chapters: List[Dict[str, Any]],
        chapter_pairs,
        source_language: str,
        target_language: str,
        threads: int,
        align_model: SentenceTransformer,
        split_model: str,
    ):
        self.source_chapters = source_chapters
        self.target_chapters = target_chapters
        self.chapter_pairs = chapter_pairs
        self.source_language = Language.from_iso_code_639_1(IsoCode639_1.from_str(source_language)) if source_language else None
        self.target_language = Language.from_iso_code_639_1(IsoCode639_1.from_str(target_language)) if target_language else None
        self.threads = threads
        self.align_model_encoder = Encoder(align_model)
        self.splitter = ChapterSplitter(split_model)
        self.language_detector: LanguageDetector = LanguageDetectorBuilder.from_all_languages().with_low_accuracy_mode().build()
        self.log = logging.getLogger(__name__)

    def _align_pair(self, source_text: str, target_text: str) -> List[List[Dict[str, str]]]:
        if not source_text.strip() or not target_text.strip():
            return []

        src_lang = self.source_language
        tgt_lang = self.target_language

        texts_to_detect = []
        if src_lang is None:
            texts_to_detect.append(source_text)
        if tgt_lang is None:
            texts_to_detect.append(target_text)
        if texts_to_detect:
            self.log.info("Detecting language...")
            try:
                detected = self.language_detector.detect_languages_in_parallel_of(texts_to_detect)
            except Exception as e:
                e.add_note("^ LanguageDetector")
                raise
            if detected:
                if src_lang is None:
                    src_lang = detected[0]
                if tgt_lang is None and len(detected) > 1:
                    tgt_lang = detected[1]

        src_paras = self.splitter.run(source_text, src_lang)   # List[List[str]]
        tgt_paras = self.splitter.run(target_text, tgt_lang)

        src_flat = [s for para in src_paras for s in para]
        tgt_flat = [s for para in tgt_paras for s in para]

        src_bounds = [0] + list(accumulate(len(p) for p in src_paras))

        try:
            aligner = Bertalign(
                model_encoder=self.align_model_encoder,
                source_sentences=src_flat,
                target_sentences=tgt_flat,
            )
            aligner.align_sents()
        except Exception as e:
            e.add_note("^ Bertalign")
            raise

        para_segments: Dict[int, List[Dict[str, str]]] = {}
        for src_indices, tgt_indices in aligner.result:
            src_seg = ' '.join(aligner.src_sents[i] for i in src_indices)
            tgt_seg = ' '.join(aligner.tgt_sents[i] for i in tgt_indices)
            para_idx = bisect_right(src_bounds, src_indices[0]) - 1
            para_segments.setdefault(para_idx, []).append({'source': src_seg, 'target': tgt_seg})

        aligned_paras = [para_segments[i] for i in sorted(para_segments)]
        return aligned_paras

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

        with progress.phase('aligning', len(pairs_to_align), "Aligning chapters"):
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
                            self.log.error(f"Error aligning chapter pair {idx}: {e}")
                            alignment = [{'source': src_text, 'target': tgt_text}]
                        output[idx]['alignment'] = alignment
                        progress.update('aligning')
            else:
                for idx, src_text, tgt_text in pairs_to_align:
                    output[idx]['alignment'] = self._align_pair(src_text, tgt_text)
                    progress.update('aligning')

        return output