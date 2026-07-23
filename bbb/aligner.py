import re
import numpy as np
from typing import List, Dict, Any
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

    def _find_paragraph_starts_in_html(self, html: str, flat_sentences: List[str], language: Language) -> List[int]:
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'img', 'figure', 'svg', 'canvas']):
            tag.decompose()

        paragraph_elems = []
        for elem in soup.descendants:
            if elem is None or not isinstance(elem, Tag):
                continue
            if elem.name == 'p' or (elem.name == 'div' and 'paragraph' in elem.get('class', [])):
                text = elem.get_text().strip()
                if text:
                    paragraph_elems.append(text)

        if not paragraph_elems:
            return [0]

        start_indices = []
        search_idx = 0
        for para_text in paragraph_elems:
            para_sents = self.splitter.run(para_text, language)
            if not para_sents or not para_sents[0]:
                continue
            first_sent = para_sents[0][0].strip()
            for idx in range(search_idx, len(flat_sentences)):
                if flat_sentences[idx].strip() == first_sent:
                    start_indices.append(idx)
                    search_idx = idx + 1
                    break
        return start_indices

    def _align_pair(self, src_html: str, tgt_html: str, src_lang, tgt_lang) -> List[List[Dict[str, str]]]:
        if not src_html.strip() or not tgt_html.strip():
            return [[{'source_html': src_html, 'target_html': tgt_html}]]

        lang_src = self._detect_language(BeautifulSoup(src_html, 'html.parser').get_text(), src_lang)
        lang_tgt = self._detect_language(BeautifulSoup(tgt_html, 'html.parser').get_text(), tgt_lang)

        extractor = HtmlSentenceTokenizer(self.splitter)

        src_sents = extractor.extract(src_html, lang_src)
        tgt_sents = extractor.extract(tgt_html, lang_tgt)

        if not src_sents or not tgt_sents:
            return [[{'source_html': src_html, 'target_html': tgt_html}]]

        src_plain = [s[0] for s in src_sents]
        tgt_plain = [s[0] for s in tgt_sents]

        para_starts = self._find_paragraph_starts_in_html(src_html, src_plain, lang_src)
        if not para_starts:
            para_starts = [0]

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

        paragraphs = []
        current_para = []
        para_idx = 0
        for s_list, t_list in raw_pairs:
            if not s_list or not t_list:
                continue
            src_html_combined = '\n'.join(src_sents[i][1] for i in s_list if i < len(src_sents))
            tgt_html_combined = '\n'.join(tgt_sents[i][1] for i in t_list if i < len(tgt_sents))

            if para_idx < len(para_starts) and any(idx >= para_starts[para_idx] for idx in s_list):
                if current_para:
                    paragraphs.append(current_para)
                    current_para = []
                para_idx += 1

            current_para.append({'source_html': src_html_combined, 'target_html': tgt_html_combined})

        if current_para:
            paragraphs.append(current_para)

        if not paragraphs:
            paragraphs = [[{'source_html': src_html, 'target_html': tgt_html}]]
        return paragraphs

    def run(self) -> List[Dict[str, Any]]:
        output = []
        for src_idx, tgt_idx in self.chapter_pairs:
            block = {'source': None, 'target': None, 'alignment': None}

            if src_idx is not None:
                ch = self.source_chapters[src_idx]
                block['source'] = {
                    'toc_path': ch['toc_path'],
                    'content_html': ch.get('content_html', '') if tgt_idx is not None else None,
                    'index': ch['index'],
                    'body_class': ch.get('body_class', ''),
                    'footnote_placeholders': ch.get('footnote_placeholders', []),
                }
            if tgt_idx is not None:
                ch = self.target_chapters[tgt_idx]
                block['target'] = {
                    'toc_path': ch['toc_path'],
                    'content_html': ch.get('content_html', '') if src_idx is not None else None,
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
                        alignment = [[{'source_html': '', 'target_html': ''}]]
                    output[idx]['alignment'] = alignment
                    progress.update('aligning')

        return output