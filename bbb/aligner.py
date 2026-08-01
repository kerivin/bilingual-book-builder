# aligner.py
import re
import numpy as np
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from bs4 import BeautifulSoup, Tag

from bbb import progress
from bbb.splitter import Splitter
from bbb.html_tokenizer import HtmlSentenceTokenizer
from bbb.constants import SRC_FN_PREFIX, TGT_FN_PREFIX
from lingua import Language, LanguageDetectorBuilder, IsoCode639_1
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
        self.align_model = align_model
        self.splitter = Splitter(split_model)
        self.language_detector = LanguageDetectorBuilder.from_all_languages().build()
        self.log = logging.getLogger(__name__)

    def _detect_language(self, text, default_lang):
        if default_lang is not None:
            return default_lang
        try:
            return self.language_detector.detect_language_of(text)
        except Exception:
            return None

    def _align_pair(self, src_html: str, tgt_html: str, src_lang, tgt_lang) -> List[Dict[str, Any]]:
        if not src_html.strip() or not tgt_html.strip():
            return [{'source_sents': [{'html': src_html, 'first': True}],
                     'target_sents': [{'html': tgt_html, 'first': True}]}]

        lang_src = self._detect_language(BeautifulSoup(src_html, 'html.parser').get_text(), src_lang)
        lang_tgt = self._detect_language(BeautifulSoup(tgt_html, 'html.parser').get_text(), tgt_lang)

        extractor = HtmlSentenceTokenizer(self.splitter)

        src_sents, src_para_starts = extractor.extract(src_html, lang_src)
        tgt_sents, tgt_para_starts = extractor.extract(tgt_html, lang_tgt)

        if not src_sents or not tgt_sents:
            return [{'source_sents': [{'html': src_html, 'first': True}],
                     'target_sents': [{'html': tgt_html, 'first': True}]}]

        src_plain = [s[0] for s in src_sents]
        tgt_plain = [s[0] for s in tgt_sents]

        try:
            bert = Bertalign(
                model_encoder=self.align_model_encoder,
                source_sentences=src_plain,
                target_sentences=tgt_plain,
            )
            bert.align_sents()
            raw_pairs = bert.result
        except Exception as e:
            self.log.warning(f"Bertalign failed: {e}, falling back to 1-to-1.")
            max_len = max(len(src_plain), len(tgt_plain))
            raw_pairs = [([i] if i < len(src_plain) else [], [i] if i < len(tgt_plain) else [])
                         for i in range(max_len)]

        # Build rows from aligned pairs, tracking which sentence indices are used
        # Each element: (src_indices, tgt_indices, src_htmls, tgt_htmls)
        aligned_rows = []
        used_src_indices = set()
        used_tgt_indices = set()
        
        for s_list, t_list in raw_pairs:
            src_indices = sorted([i for i in s_list if i < len(src_sents)])
            tgt_indices = sorted([i for i in t_list if i < len(tgt_sents)])
            
            if src_indices or tgt_indices:
                aligned_rows.append({
                    'src_indices': src_indices,
                    'tgt_indices': tgt_indices
                })
                used_src_indices.update(src_indices)
                used_tgt_indices.update(tgt_indices)
        
        # Find unmatched sentences
        unmatched_src = sorted(set(range(len(src_sents))) - used_src_indices)
        unmatched_tgt = sorted(set(range(len(tgt_sents))) - used_tgt_indices)
        
        # Merge unmatched sentences into the aligned rows
        # Group consecutive unmatched sentences and insert them at the right position
        final_rows = []
        unmatched_src_ptr = 0
        unmatched_tgt_ptr = 0
        
        for row in aligned_rows:
            # Add any unmatched source sentences that come before this aligned row
            src_before = []
            while unmatched_src_ptr < len(unmatched_src) and unmatched_src[unmatched_src_ptr] < row['src_indices'][0]:
                src_before.append(unmatched_src[unmatched_src_ptr])
                unmatched_src_ptr += 1
            
            # Add any unmatched target sentences that come before this aligned row
            tgt_before = []
            while unmatched_tgt_ptr < len(unmatched_tgt) and unmatched_tgt[unmatched_tgt_ptr] < row['tgt_indices'][0]:
                tgt_before.append(unmatched_tgt[unmatched_tgt_ptr])
                unmatched_tgt_ptr += 1
            
            # If there are unmatched sentences before this row, add them as a combined row
            if src_before or tgt_before:
                final_rows.append({
                    'source_sents': [{
                        'html': src_sents[i][1],
                        'first': i in src_para_starts
                    } for i in src_before],
                    'target_sents': [{
                        'html': tgt_sents[i][1],
                        'first': i in tgt_para_starts
                    } for i in tgt_before]
                })
            
            # Add the aligned row
            final_rows.append({
                'source_sents': [{
                    'html': src_sents[i][1],
                    'first': i in src_para_starts
                } for i in row['src_indices']],
                'target_sents': [{
                    'html': tgt_sents[i][1],
                    'first': i in tgt_para_starts
                } for i in row['tgt_indices']]
            })
        
        # Add remaining unmatched sentences at the end
        remaining_src = unmatched_src[unmatched_src_ptr:]
        remaining_tgt = unmatched_tgt[unmatched_tgt_ptr:]
        if remaining_src or remaining_tgt:
            final_rows.append({
                'source_sents': [{
                    'html': src_sents[i][1],
                    'first': i in src_para_starts
                } for i in remaining_src],
                'target_sents': [{
                    'html': tgt_sents[i][1],
                    'first': i in tgt_para_starts
                } for i in remaining_tgt]
            })
        
        rows = final_rows

        if not rows:
            rows = [{'source_sents': [{'html': src_html, 'first': True}],
                     'target_sents': [{'html': tgt_html, 'first': True}]}]
        return rows

    def run(self) -> List[Dict[str, Any]]:
        output = []
        for src_idx, tgt_idx in self.chapter_pairs:
            block = {'source': None, 'target': None, 'alignment': None}

            if src_idx is not None:
                ch = self.source_chapters[src_idx]
                block['source'] = {
                    'toc_path': ch['toc_path'],
                    'content_html': ch.get('content_html', ''),
                    'index': ch['index'],
                    'body_class': ch.get('body_class', ''),
                    'footnote_placeholders': ch.get('footnote_placeholders', []),
                }
            if tgt_idx is not None:
                ch = self.target_chapters[tgt_idx]
                block['target'] = {
                    'toc_path': ch['toc_path'],
                    'content_html': ch.get('content_html', ''),
                    'index': ch['index'],
                    'body_class': ch.get('body_class', ''),
                    'footnote_placeholders': ch.get('footnote_placeholders', []),
                }
            output.append(block)

        tasks = []
        for idx, (src_idx, tgt_idx) in enumerate(self.chapter_pairs):
            if src_idx is not None and tgt_idx is not None:
                src_html = self.source_chapters[src_idx]['content_html']
                tgt_html = self.target_chapters[tgt_idx]['content_html']
                tasks.append((idx, src_html, tgt_html))

        with progress.phase('aligning', len(tasks), "Aligning chapters"):
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
                    progress.update('aligning')

        return output