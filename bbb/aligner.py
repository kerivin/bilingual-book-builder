from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from itertools import accumulate
from bisect import bisect_right
import re

from bbb import progress
from bbb.splitter import Splitter
from bbb.constants import SRC_FN_PREFIX, TGT_FN_PREFIX
from bbb.html_tokenizer import rebuild_sentence
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
                    src_footnote_refs=None, tgt_footnote_refs=None,
                    src_tokens=None, tgt_tokens=None) -> List[Dict[str, Any]]:
        if not source_text.strip() or not target_text.strip():
            return [[{'source': source_text, 'target': target_text,
                      'source_html': source_text, 'target_html': target_text}]]

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
                self.log.warning(f"Language detection failed: {e}")
                detected = []
            if detected:
                if src_lang is None:
                    src_lang = detected[0]
                if tgt_lang is None and len(detected) > 1:
                    tgt_lang = detected[1]
        if src_lang is None:
            src_lang = Language.ENGLISH
        if tgt_lang is None:
            tgt_lang = Language.ENGLISH

        def process_side(text, prefix, fn_refs, lang, token_list):
            token_pattern = re.compile(rf'\s*{re.escape(prefix)}FNREF_(\d+)\s*')

            if token_list:
                all_flat = []
                all_clean = []
                all_occurrences = []
                all_htmls = []
                all_para_sent_counts = []

                for para_tokens in token_list:
                    if not para_tokens:
                        continue
                    orig_text = ''.join(t.content for t in para_tokens if t.kind == 'text')
                    if not orig_text.strip():
                        continue

                    # Split the original text – the splitter will handle newlines correctly
                    try:
                        para_split = self.splitter.run(orig_text, lang)
                    except Exception as e:
                        self.log.warning(f"Sentence splitting failed: {e}")
                        para_split = []

                    if para_split:
                        # Flatten all sentences from all sub-paragraphs
                        sentences = [s for sublist in para_split for s in sublist]
                    else:
                        sentences = [orig_text]

                    pos = 0
                    para_flat = []
                    para_clean = []
                    para_occurrences = []
                    para_htmls = []
                    for sent in sentences:
                        idx = orig_text.find(sent, pos)
                        if idx == -1:
                            idx = orig_text.find(sent.strip(), pos)
                        if idx == -1:
                            idx = pos
                        start = idx
                        end = idx + len(sent)
                        pos = end

                        found = token_pattern.findall(sent)
                        sent_tokens = []
                        for num in found:
                            token_str = f'{prefix}FNREF_{num}'
                            fn_info = next((fn for fn in (fn_refs or []) if fn['token'] == token_str), None)
                            if fn_info:
                                sent_tokens.append({'token': token_str, 'target_id': fn_info['target_id']})
                        clean = token_pattern.sub(' ', sent).strip()

                        para_flat.append(sent)
                        para_clean.append(clean)
                        para_occurrences.append(sent_tokens)

                        html, _ = rebuild_sentence(para_tokens, start, end)
                        para_htmls.append(html)

                    all_flat.extend(para_flat)
                    all_clean.extend(para_clean)
                    all_occurrences.extend(para_occurrences)
                    all_htmls.extend(para_htmls)
                    all_para_sent_counts.append(len(sentences))

                paragraphs = []
                idx = 0
                for count in all_para_sent_counts:
                    paragraphs.append(all_flat[idx:idx+count])
                    idx += count
                return paragraphs, all_flat, all_clean, all_occurrences, all_htmls

            # Fallback: no token list
            paragraphs = self.splitter.run(text, lang)
            if not paragraphs:
                return [], [], [], [], []
            all_flat = [s for p in paragraphs for s in p]
            all_clean = []
            all_occurrences = []
            for s in all_flat:
                found = token_pattern.findall(s)
                sent_tokens = []
                for num in found:
                    token_str = f'{prefix}FNREF_{num}'
                    fn_info = next((fn for fn in (fn_refs or []) if fn['token'] == token_str), None)
                    if fn_info:
                        sent_tokens.append({'token': token_str, 'target_id': fn_info['target_id']})
                clean = token_pattern.sub(' ', s).strip()
                all_clean.append(clean)
                all_occurrences.append(sent_tokens)
            return paragraphs, all_flat, all_clean, all_occurrences, all_flat

        src_paras, src_flat, src_clean, src_sent_tokens, src_htmls = process_side(
            source_text, SRC_FN_PREFIX, src_footnote_refs, src_lang, src_tokens)
        tgt_paras, tgt_flat, tgt_clean, tgt_sent_tokens, tgt_htmls = process_side(
            target_text, TGT_FN_PREFIX, tgt_footnote_refs, tgt_lang, tgt_tokens)

        if not src_flat or not tgt_flat:
            return [[{'source': source_text, 'target': target_text,
                      'source_html': source_text, 'target_html': target_text}]]

        src_bounds = [0] + list(accumulate(len(p) for p in src_paras))

        try:
            aligner = Bertalign(
                model_encoder=self.align_model_encoder,
                source_sentences=src_clean,
                target_sentences=tgt_clean,
            )
            aligner.align_sents()
        except Exception as e:
            self.log.warning(f"Bertalign failed: {e}, using fallback alignment.")
            return [[{'source': source_text, 'target': target_text,
                      'source_html': source_text, 'target_html': target_text}]]

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
                        'source_html': src_htmls[i] if i < len(src_htmls) else src_flat[i],
                        'target_html': '',
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
                        seg_list[-1]['target_html'] = tgt_htmls[j] if j < len(tgt_htmls) else tgt_flat[j]
                        seg_list[-1]['target_footnote_occurrences'] = tgt_sent_tokens[j]
                    else:
                        seg_list.append({
                            'source': '',
                            'target': tgt_flat[j],
                            'source_html': '',
                            'target_html': tgt_htmls[j] if j < len(tgt_htmls) else tgt_flat[j],
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
            src_html = '\n'.join(src_htmls[i] for i in src_indices) if src_indices else ''
            tgt_html = '\n'.join(tgt_htmls[i] for i in tgt_indices) if tgt_indices else ''

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
                'source_html': src_html,
                'target_html': tgt_html,
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
                    'toc_path': ch['toc_path'],
                    'text': ch['full_text'] if target_index is None else None,
                    'index': ch['index'],
                    'footnote_refs': ch.get('footnote_refs', []),
                    'paragraph_tokens': ch.get('paragraph_tokens', None),
                    'body_class': ch.get('body_class', '')
                }

            if target_index is not None:
                ch = self.target_chapters[target_index]
                block['target'] = {
                    'toc_path': ch['toc_path'],
                    'text': ch['full_text'] if source_index is None else None,
                    'index': ch['index'],
                    'footnote_refs': ch.get('footnote_refs', []),
                    'paragraph_tokens': ch.get('paragraph_tokens', None),
                    'body_class': ch.get('body_class', '')
                }

            output.append(block)

        tasks = []
        for idx, (src_idx, tgt_idx) in enumerate(self.chapter_pairs):
            if src_idx is not None and tgt_idx is not None:
                src_ch = self.source_chapters[src_idx]
                tgt_ch = self.target_chapters[tgt_idx]
                src_text = src_ch['full_text']
                tgt_text = tgt_ch['full_text']
                src_refs = src_ch.get('footnote_refs', [])
                tgt_refs = tgt_ch.get('footnote_refs', [])
                src_tokens = src_ch.get('paragraph_tokens', None)
                tgt_tokens = tgt_ch.get('paragraph_tokens', None)
                tasks.append((idx, src_text, tgt_text, src_refs, tgt_refs, src_tokens, tgt_tokens))

        with progress.phase('aligning', len(tasks), "Aligning chapters"):
            max_workers = max(1, self.threads)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_idx = {
                    executor.submit(self._align_pair, src, tgt, src_refs, tgt_refs, src_tok, tgt_tok): (idx, src, tgt)
                    for idx, src, tgt, src_refs, tgt_refs, src_tok, tgt_tok in tasks
                }
                for future in as_completed(future_to_idx):
                    idx, src_text, tgt_text = future_to_idx[future]
                    try:
                        alignment = future.result()
                    except Exception as e:
                        self.log.error(f"Error aligning chapter pair {idx}: {e}")
                        alignment = [[{'source': src_text, 'target': tgt_text,
                                       'source_html': src_text, 'target_html': tgt_text}]]
                    output[idx]['alignment'] = alignment
                    progress.update('aligning')

        return output