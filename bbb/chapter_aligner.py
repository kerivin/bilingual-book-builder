from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

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

    def _align_pair(self, source_text: str, target_text: str) -> List[Dict[str, str]]:
        if not source_text.strip() or not target_text.strip():
            return []

        texts_to_detect_language = []
        if self.source_language is None:
            texts_to_detect_language.append(source_text)
        if self.target_language is None:
            texts_to_detect_language.append(target_text)

        if texts_to_detect_language:
            languages = []
            self.log.info(f"Detecting language...")
            try:
                languages = self.language_detector.detect_languages_in_parallel_of(texts_to_detect_language)
            except Exception as e:
                e.add_note("^ LanguageDetector")
                languages = []

            if languages:
                if not self.source_language:
                    self.source_language = languages[0]
                if not self.target_language:
                    self.target_language = languages[1] if len(languages) > 1 else languages[0]

        try:
            source_sentences = self.splitter.run(source_text, self.source_language)
            target_sentences = self.splitter.run(target_text, self.target_language)
        except Exception as e:
            e.add_note("^ ChapterSplitter")
            raise

        try:
            aligner = Bertalign(
                model_encoder = self.align_model_encoder,
                source_sentences = source_sentences,
                target_sentences = target_sentences,
                # progress_callback = progress.get_callback()
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
            e.add_note("^ Bertalign")
            raise

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