from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from bs4 import BeautifulSoup

from bbb.splitter import Splitter
from bbb.html_tokenizer import HtmlSentenceTokenizer
from bbb.progress import ProgressReporter
from lingua import Language, LanguageDetectorBuilder, IsoCode639_1
from sentence_transformers import SentenceTransformer
from bertalign import Bertalign
from bertalign.encoder import Encoder


def _single_row(src_html: str, tgt_html: str) -> List[Dict[str, Any]]:
    return [{'source_sents': [{'html': src_html, 'first': True}],
             'target_sents': [{'html': tgt_html, 'first': True}]}]


def merge_rows(aligned_rows, src_sents, tgt_sents, src_para_starts, tgt_para_starts) -> List[Dict[str, Any]]:
    used_src = set()
    used_tgt = set()
    for row in aligned_rows:
        used_src.update(row['src_indices'])
        used_tgt.update(row['tgt_indices'])

    unmatched_src = sorted(set(range(len(src_sents))) - used_src)
    unmatched_tgt = sorted(set(range(len(tgt_sents))) - used_tgt)

    next_src_boundary = [float('inf')] * len(aligned_rows)
    next_tgt_boundary = [float('inf')] * len(aligned_rows)
    cur_src = float('inf')
    cur_tgt = float('inf')
    for i in range(len(aligned_rows) - 1, -1, -1):
        next_src_boundary[i] = cur_src
        next_tgt_boundary[i] = cur_tgt
        if aligned_rows[i]['src_indices']:
            cur_src = aligned_rows[i]['src_indices'][0]
        if aligned_rows[i]['tgt_indices']:
            cur_tgt = aligned_rows[i]['tgt_indices'][0]

    def make_row(src_idxs, tgt_idxs):
        return {
            'source_sents': [{'html': src_sents[i][1], 'first': i in src_para_starts} for i in src_idxs],
            'target_sents': [{'html': tgt_sents[i][1], 'first': i in tgt_para_starts} for i in tgt_idxs],
        }

    rows = []
    src_ptr = 0
    tgt_ptr = 0
    for i, row in enumerate(aligned_rows):
        row_src_first = row['src_indices'][0] if row['src_indices'] else next_src_boundary[i]
        row_tgt_first = row['tgt_indices'][0] if row['tgt_indices'] else next_tgt_boundary[i]

        src_before = []
        while src_ptr < len(unmatched_src) and unmatched_src[src_ptr] < row_src_first:
            src_before.append(unmatched_src[src_ptr])
            src_ptr += 1
        tgt_before = []
        while tgt_ptr < len(unmatched_tgt) and unmatched_tgt[tgt_ptr] < row_tgt_first:
            tgt_before.append(unmatched_tgt[tgt_ptr])
            tgt_ptr += 1

        if src_before or tgt_before:
            rows.append(make_row(src_before, tgt_before))
        rows.append(make_row(row['src_indices'], row['tgt_indices']))

    remaining_src = unmatched_src[src_ptr:]
    remaining_tgt = unmatched_tgt[tgt_ptr:]
    if remaining_src or remaining_tgt:
        rows.append(make_row(remaining_src, remaining_tgt))

    return rows


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
        progress_reporter=None,
    ):
        self.source_chapters = source_chapters
        self.target_chapters = target_chapters
        self.chapter_pairs = chapter_pairs
        self.source_language = self._parse_language(source_language)
        self.target_language = self._parse_language(target_language)
        self.threads = threads
        self.align_model_encoder = Encoder(align_model)
        self.splitter = Splitter(split_model)
        self.progress = progress_reporter or ProgressReporter()
        known = [l for l in (self.source_language, self.target_language) if l is not None]
        if len(known) == 2:
            self.language_detector = LanguageDetectorBuilder.from_languages(*known).build()
        else:
            self.language_detector = LanguageDetectorBuilder.from_all_languages().build()
        self.log = logging.getLogger(__name__)

    @staticmethod
    def _parse_language(code: str):
        if not code:
            return None
        try:
            return Language.from_iso_code_639_1(IsoCode639_1.from_str(code))
        except ValueError:
            return None

    def _detect_language(self, text, default_lang):
        if default_lang is not None:
            return default_lang
        try:
            return self.language_detector.detect_language_of(text)
        except Exception:
            return None

    def _run_bertalign(self, src_plain, tgt_plain):
        try:
            bert = Bertalign(
                model_encoder=self.align_model_encoder,
                source_sentences=src_plain,
                target_sentences=tgt_plain,
            )
            bert.align_sents()
            return bert.result
        except Exception as e:
            self.log.warning(f"Bertalign failed: {e}, falling back to 1-to-1.")
            max_len = max(len(src_plain), len(tgt_plain))
            return [([i] if i < len(src_plain) else [], [i] if i < len(tgt_plain) else [])
                    for i in range(max_len)]

    def _align_pair(self, src_html: str, tgt_html: str, src_lang, tgt_lang) -> List[Dict[str, Any]]:
        if not src_html.strip() or not tgt_html.strip():
            return _single_row(src_html, tgt_html)

        lang_src = self._detect_language(BeautifulSoup(src_html, 'html.parser').get_text(), src_lang)
        lang_tgt = self._detect_language(BeautifulSoup(tgt_html, 'html.parser').get_text(), tgt_lang)

        extractor = HtmlSentenceTokenizer(self.splitter)

        src_sents, src_para_starts = extractor.extract(src_html, lang_src)
        tgt_sents, tgt_para_starts = extractor.extract(tgt_html, lang_tgt)

        if not src_sents or not tgt_sents:
            return _single_row(src_html, tgt_html)

        src_plain = [s[0] for s in src_sents]
        tgt_plain = [s[0] for s in tgt_sents]

        raw_pairs = self._run_bertalign(src_plain, tgt_plain)

        aligned_rows = []
        for s_list, t_list in raw_pairs:
            src_indices = sorted([i for i in s_list if i < len(src_sents)])
            tgt_indices = sorted([i for i in t_list if i < len(tgt_sents)])
            if src_indices or tgt_indices:
                aligned_rows.append({'src_indices': src_indices, 'tgt_indices': tgt_indices})

        rows = merge_rows(aligned_rows, src_sents, tgt_sents, src_para_starts, tgt_para_starts)
        return rows or _single_row(src_html, tgt_html)

    def run(self) -> List[Dict[str, Any]]:
        output = []
        tasks = []
        for idx, (src_idx, tgt_idx) in enumerate(self.chapter_pairs):
            block = {'source': None, 'target': None, 'alignment': None}
            if src_idx is not None:
                block['source'] = self.source_chapters[src_idx]
            if tgt_idx is not None:
                block['target'] = self.target_chapters[tgt_idx]
            output.append(block)

            if src_idx is not None and tgt_idx is not None:
                src_html = self.source_chapters[src_idx]['content_html']
                tgt_html = self.target_chapters[tgt_idx]['content_html']
                tasks.append((idx, src_html, tgt_html))

        with self.progress.phase('aligning', len(tasks), "Aligning chapters"):
            max_workers = max(1, self.threads)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_idx = {
                    executor.submit(self._align_pair, src, tgt, self.source_language, self.target_language): idx
                    for idx, src, tgt in tasks
                }
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        alignment = future.result()
                    except Exception as e:
                        self.log.error(f"Error aligning chapter pair {idx}: {e}")
                        alignment = [{'source_sents': [], 'target_sents': []}]
                    output[idx]['alignment'] = alignment
                    self.progress.update('aligning')

        return output