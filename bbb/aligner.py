from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from itertools import accumulate
from bisect import bisect_right
import re

from bbb import progress
from bbb.splitter import Splitter
from bbb.constants import SRC_FN_PREFIX, TGT_FN_PREFIX
from lingua import LanguageDetectorBuilder, LanguageDetector, Language, IsoCode639_1
from sentence_transformers import SentenceTransformer
from bertalign import Bertalign
from bertalign.encoder import Encoder

class Aligner:
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
        self.splitter = Splitter(split_model)
        self.language_detector: LanguageDetector = LanguageDetectorBuilder.from_all_languages().build()
        self.log = logging.getLogger(__name__)

    def _align_pair(self, source_text: str, target_text: str,
                    src_footnote_refs=None, tgt_footnote_refs=None) -> List[Dict[str, Any]]:
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

        def process_side(text, prefix, fn_refs, lang):
            if lang is None:
                return [], [], [], []
            paragraphs = self.splitter.run(text, lang)
            flat_sentences = [s for p in paragraphs for s in p]
            token_pattern = re.compile(rf'\s*{re.escape(prefix)}FNREF_(\d+)\s*')
            clean_sentences = []
            token_occurrences = []
            for sent in flat_sentences:
                found = token_pattern.findall(sent)
                sent_tokens = []
                for num in found:
                    token_str = f'{prefix}FNREF_{num}'
                    fn_info = next((fn for fn in (fn_refs or []) if fn['token'] == token_str), None)
                    if fn_info:
                        sent_tokens.append({'token': token_str, 'target_id': fn_info['target_id']})
                token_occurrences.append(sent_tokens)
                clean_sentences.append(token_pattern.sub(' ', sent).strip())
            return paragraphs, flat_sentences, clean_sentences, token_occurrences

        src_paras, src_flat, src_clean, src_sent_tokens = process_side(
            source_text, SRC_FN_PREFIX, src_footnote_refs, src_lang)
        tgt_paras, tgt_flat, tgt_clean, tgt_sent_tokens = process_side(
            target_text, TGT_FN_PREFIX, tgt_footnote_refs, tgt_lang)

        if not src_flat or not tgt_flat:
            return [[{'source': source_text, 'target': target_text}]]

        src_bounds = [0] + list(accumulate(len(p) for p in src_paras))

        try:
            aligner = Bertalign(
                model_encoder=self.align_model_encoder,
                source_sentences=src_clean,
                target_sentences=tgt_clean,
            )
            aligner.align_sents()
        except Exception as e:
            e.add_note("^ Bertalign")
            raise

        matched_src = set()
        matched_tgt = set()
        for src_indices, tgt_indices in aligner.result:
            if src_indices:
                matched_src.update(src_indices)
            if tgt_indices:
                matched_tgt.update(tgt_indices)

        para_segments: Dict[int, List[Dict[str, Any]]] = {}
        next_src = 0
        next_tgt = 0
        current_para_idx = 0

        def add_unmatched_src(from_idx, to_idx):
            nonlocal next_src, current_para_idx
            for i in range(from_idx, to_idx + 1):
                if i not in matched_src:
                    para_idx = bisect_right(src_bounds, i) - 1
                    seg = {
                        'source': src_flat[i],
                        'target': '',
                        'source_footnote_occurrences': src_sent_tokens[i],
                        'target_footnote_occurrences': []
                    }
                    para_segments.setdefault(para_idx, []).append(seg)
                    current_para_idx = para_idx
            next_src = to_idx + 1

        def add_unmatched_tgt(from_idx, to_idx):
            nonlocal next_tgt, current_para_idx
            for j in range(from_idx, to_idx + 1):
                if j not in matched_tgt:
                    seg_list = para_segments.setdefault(current_para_idx, [])
                    if seg_list and seg_list[-1].get('target', None) is None:
                        seg_list[-1]['target'] = tgt_flat[j]
                        seg_list[-1]['target_footnote_occurrences'] = tgt_sent_tokens[j]
                    else:
                        seg_list.append({
                            'source': '',
                            'target': tgt_flat[j],
                            'source_footnote_occurrences': [],
                            'target_footnote_occurrences': tgt_sent_tokens[j]
                        })
            next_tgt = to_idx + 1

        for src_indices, tgt_indices in aligner.result:
            if src_indices:
                match_src_start = min(src_indices)
                if next_src < match_src_start:
                    add_unmatched_src(next_src, match_src_start - 1)
            else:
                if next_src < len(src_flat):
                    add_unmatched_src(next_src, len(src_flat) - 1)

            if tgt_indices:
                match_tgt_start = min(tgt_indices)
                if next_tgt < match_tgt_start:
                    add_unmatched_tgt(next_tgt, match_tgt_start - 1)
            else:
                if next_tgt < len(tgt_flat):
                    add_unmatched_tgt(next_tgt, len(tgt_flat) - 1)

            src_seg = '\n'.join(src_flat[i] for i in src_indices) if src_indices else ''
            tgt_seg = '\n'.join(tgt_flat[i] for i in tgt_indices) if tgt_indices else ''

            src_occurrences = []
            for i in (src_indices or []):
                src_occurrences.extend(src_sent_tokens[i])
            tgt_occurrences = []
            for i in (tgt_indices or []):
                tgt_occurrences.extend(tgt_sent_tokens[i])

            if src_indices:
                para_idx = bisect_right(src_bounds, min(src_indices)) - 1
                current_para_idx = para_idx

            para_segments.setdefault(current_para_idx, []).append({
                'source': src_seg,
                'target': tgt_seg,
                'source_footnote_occurrences': src_occurrences,
                'target_footnote_occurrences': tgt_occurrences
            })

            if src_indices:
                next_src = max(src_indices) + 1
            if tgt_indices:
                next_tgt = max(tgt_indices) + 1

        if next_src < len(src_flat):
            add_unmatched_src(next_src, len(src_flat) - 1)
        if next_tgt < len(tgt_flat):
            add_unmatched_tgt(next_tgt, len(tgt_flat) - 1)

        aligned_paras = [para_segments[i] for i in sorted(para_segments)]
        return aligned_paras

    def run(self) -> List[Dict[str, Any]]:
        output = []
        for source_index, target_index in self.chapter_pairs:
            block = {'source': None, 'target': None, 'alignment': None}

            if source_index is not None:
                ch = self.source_chapters[source_index]
                block['source'] = {
                    'display_path': ch['display_path'],
                    'toc_path': ch['toc_path'],
                    'text': ch['full_text'] if target_index is None else None,
                    'index': ch['index'],
                    'footnote_refs': ch.get('footnote_refs', []),
                }

            if target_index is not None:
                ch = self.target_chapters[target_index]
                block['target'] = {
                    'display_path': ch['display_path'],
                    'toc_path': ch['toc_path'],
                    'text': ch['full_text'] if source_index is None else None,
                    'index': ch['index'],
                    'footnote_refs': ch.get('footnote_refs', []),
                }

            output.append(block)

        tasks = []
        for idx, (src_idx, tgt_idx) in enumerate(self.chapter_pairs):
            if src_idx is not None and tgt_idx is not None:
                src_text = self.source_chapters[src_idx]['full_text']
                tgt_text = self.target_chapters[tgt_idx]['full_text']
                src_refs = self.source_chapters[src_idx].get('footnote_refs', [])
                tgt_refs = self.target_chapters[tgt_idx].get('footnote_refs', [])
                tasks.append((idx, src_text, tgt_text, src_refs, tgt_refs))

        with progress.phase('aligning', len(tasks), "Aligning chapters"):
            max_workers = max(1, self.threads)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_idx = {
                    executor.submit(self._align_pair, src, tgt, src_refs, tgt_refs): (idx, src, tgt)
                    for idx, src, tgt, src_refs, tgt_refs in tasks
                }
                for future in as_completed(future_to_idx):
                    idx, src_text, tgt_text = future_to_idx[future]
                    try:
                        alignment = future.result()
                    except Exception as e:
                        self.log.error(f"Error aligning chapter pair {idx}: {e}")
                        alignment = [[{'source': src_text, 'target': tgt_text}]]
                    output[idx]['alignment'] = alignment
                    progress.update('aligning')

        return output