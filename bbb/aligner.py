import numpy as np
import html
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
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
        self.align_model = align_model
        self.splitter = Splitter(split_model)
        self.language_detector: LanguageDetector = LanguageDetectorBuilder.from_all_languages().build()
        self.log = logging.getLogger(__name__)

    def _detect_language(self, text, default_lang):
        if default_lang is not None:
            return default_lang
        try:
            return self.language_detector.detect_language_of(text)
        except Exception:
            return None

    def _align_pair(self, source_text: str, target_text: str,
                    src_footnote_refs=None, tgt_footnote_refs=None,
                    src_tokens=None, tgt_tokens=None) -> List[Dict[str, Any]]:
        if not source_text.strip() or not target_text.strip():
            return [[{'source': source_text, 'target': target_text,
                      'source_html': source_text, 'target_html': target_text}]]

        src_lang = self._detect_language(source_text, self.source_language)
        tgt_lang = self._detect_language(target_text, self.target_language)

        PARA_PLACEHOLDER = '\uE001'

        def process_side(token_list, prefix, fn_refs, lang, full_text):
            all_flat = []
            all_clean = []
            all_htmls = []
            all_occs = []

            token_pattern = re.compile(rf'\s*{re.escape(prefix)}FNREF_(\d+)\s*')
            fn_lookup = {}
            for fn in (fn_refs or []):
                fn_lookup[fn['token']] = fn['target_id']

            if token_list is None:
                try:
                    paragraphs = self.splitter.run(full_text, lang)
                except Exception:
                    paragraphs = [[full_text]]
                if not paragraphs:
                    paragraphs = [[full_text]]

                all_flat = [s for p in paragraphs for s in p]
                for sent in all_flat:
                    found = token_pattern.findall(sent)
                    occ = []
                    clean = sent
                    for num in found:
                        token_str = f'{prefix}FNREF_{num}'
                        if token_str in fn_lookup:
                            occ.append({'token': token_str, 'target_id': fn_lookup[token_str]})
                        clean = clean.replace(token_str, ' ')
                    clean = re.sub(r'\s+', ' ', clean).strip()
                    all_clean.append(clean)
                    all_occs.append(occ)
                    all_htmls.append(html.escape(sent))
                return all_flat, all_clean, all_htmls, all_occs

            for para_tokens in token_list:
                if not para_tokens:
                    continue
                orig_text = ''.join(t.content for t in para_tokens if t.kind == 'text')
                if not orig_text.strip():
                    continue

                safe_text = orig_text.replace('\n\n', PARA_PLACEHOLDER)

                try:
                    split = self.splitter.run(safe_text, lang)
                except Exception:
                    split = []
                if split:
                    sentences = [s for sub in split for s in sub]
                else:
                    sentences = [safe_text]
                sentences = [s.replace(PARA_PLACEHOLDER, '\n\n') for s in sentences]

                pos = 0
                for sent in sentences:
                    idx = orig_text.find(sent, pos)
                    if idx == -1:
                        idx = orig_text.find(sent.strip(), pos)
                    if idx == -1:
                        idx = pos
                    start, end = idx, idx + len(sent)
                    pos = end

                    occ = []
                    clean = sent
                    for m in token_pattern.finditer(sent):
                        num = m.group(1)
                        token_str = f'{prefix}FNREF_{num}'
                        if token_str in fn_lookup:
                            occ.append({'token': token_str, 'target_id': fn_lookup[token_str]})
                        clean = clean.replace(m.group(), ' ')
                    clean = re.sub(r'\s+', ' ', clean).strip()

                    all_flat.append(sent)
                    all_clean.append(clean)
                    all_occs.append(occ)

                    html_str, _ = rebuild_sentence(para_tokens, start, end)
                    all_htmls.append(html_str)

            return all_flat, all_clean, all_htmls, all_occs

        src_flat, src_clean, src_htmls, src_occs = process_side(
            src_tokens, SRC_FN_PREFIX, src_footnote_refs, src_lang, source_text)
        tgt_flat, tgt_clean, tgt_htmls, tgt_occs = process_side(
            tgt_tokens, TGT_FN_PREFIX, tgt_footnote_refs, tgt_lang, target_text)

        if not src_flat or not tgt_flat:
            return [[{'source': source_text, 'target': target_text,
                      'source_html': source_text, 'target_html': target_text}]]

        src_embs = self.align_model.encode(src_clean, convert_to_numpy=True, show_progress_bar=False)
        tgt_embs = self.align_model.encode(tgt_clean, convert_to_numpy=True, show_progress_bar=False)
        src_embs = src_embs / (np.linalg.norm(src_embs, axis=1, keepdims=True) + 1e-9)
        tgt_embs = tgt_embs / (np.linalg.norm(tgt_embs, axis=1, keepdims=True) + 1e-9)

        try:
            bert = Bertalign(
                model_encoder=self.align_model_encoder,
                source_sentences=src_clean,
                target_sentences=tgt_clean,
            )
            bert.align_sents()
            raw_pairs = bert.result
        except Exception as e:
            self.log.warning(f"Bertalign failed: {e}, falling back to 1-to-1 alignment.")
            max_len = max(len(src_flat), len(tgt_flat))
            raw_pairs = [([i] if i < len(src_flat) else [], [i] if i < len(tgt_flat) else [])
                         for i in range(max_len)]

        HIGH_THRESHOLD = 0.6
        AVG_THRESHOLD = 0.4

        filtered_pairs = []
        for s_list, t_list in raw_pairs:
            if not s_list or not t_list:
                continue
            max_sim = -1.0
            for si in s_list:
                for ti in t_list:
                    sim = np.dot(src_embs[si], tgt_embs[ti])
                    if sim > max_sim:
                        max_sim = sim
            if max_sim >= HIGH_THRESHOLD:
                filtered_pairs.append((sorted(s_list), sorted(t_list)))
                continue
            src_group_emb = np.mean(src_embs[s_list], axis=0)
            tgt_group_emb = np.mean(tgt_embs[t_list], axis=0)
            avg_sim = np.dot(src_group_emb, tgt_group_emb)
            if avg_sim >= AVG_THRESHOLD:
                filtered_pairs.append((sorted(s_list), sorted(t_list)))

        matched_src = set()
        matched_tgt = set()
        for s_list, t_list in filtered_pairs:
            matched_src.update(s_list)
            matched_tgt.update(t_list)

        segments = []
        i, j = 0, 0
        S, T = len(src_flat), len(tgt_flat)

        # Buffers for consecutive unmatched sentences
        unmatched_src_buf = []   # list of (text, html, occ)
        unmatched_tgt_buf = []

        def flush_unmatched():
            nonlocal unmatched_src_buf, unmatched_tgt_buf
            if not unmatched_src_buf and not unmatched_tgt_buf:
                return
            src_text = '\n'.join(s[0] for s in unmatched_src_buf) if unmatched_src_buf else ''
            tgt_text = '\n'.join(t[0] for t in unmatched_tgt_buf) if unmatched_tgt_buf else ''
            src_html = '\n'.join(s[1] for s in unmatched_src_buf) if unmatched_src_buf else ''
            tgt_html = '\n'.join(t[1] for t in unmatched_tgt_buf) if unmatched_tgt_buf else ''
            src_occ = [o for s in unmatched_src_buf for o in s[2]]
            tgt_occ = [o for t in unmatched_tgt_buf for o in t[2]]
            segments.append({
                'source': src_text,
                'target': tgt_text,
                'source_html': src_html,
                'target_html': tgt_html,
                'source_footnote_occurrences': src_occ,
                'target_footnote_occurrences': tgt_occ
            })
            unmatched_src_buf.clear()
            unmatched_tgt_buf.clear()

        def find_pair_with_src(idx):
            for s_list, t_list in filtered_pairs:
                if s_list and s_list[0] == idx:
                    return (s_list, t_list)
            return None

        def find_pair_with_tgt(idx):
            for s_list, t_list in filtered_pairs:
                if t_list and t_list[0] == idx:
                    return (s_list, t_list)
            return None

        while i < S or j < T:
            # Buffer unmatched sentences until we hit a matched one
            if i < S and i not in matched_src:
                unmatched_src_buf.append((src_flat[i], src_htmls[i], src_occs[i]))
                i += 1
                continue
            if j < T and j not in matched_tgt:
                unmatched_tgt_buf.append((tgt_flat[j], tgt_htmls[j], tgt_occs[j]))
                j += 1
                continue

            # Both are matched now – flush buffered unmatched, then output the matched pair
            flush_unmatched()

            pair = find_pair_with_src(i) or find_pair_with_tgt(j)
            if pair is None:
                # fallback: treat as unmatched
                if i < S:
                    unmatched_src_buf.append((src_flat[i], src_htmls[i], src_occs[i]))
                    i += 1
                if j < T:
                    unmatched_tgt_buf.append((tgt_flat[j], tgt_htmls[j], tgt_occs[j]))
                    j += 1
                continue

            s_list, t_list = pair
            seg_src = '\n'.join(src_flat[idx] for idx in s_list)
            seg_tgt = '\n'.join(tgt_flat[idx] for idx in t_list)
            seg_src_html = '\n'.join(src_htmls[idx] for idx in s_list)
            seg_tgt_html = '\n'.join(tgt_htmls[idx] for idx in t_list)
            seg_src_occ = [o for idx in s_list for o in src_occs[idx]]
            seg_tgt_occ = [o for idx in t_list for o in tgt_occs[idx]]
            segments.append({
                'source': seg_src,
                'target': seg_tgt,
                'source_html': seg_src_html,
                'target_html': seg_tgt_html,
                'source_footnote_occurrences': seg_src_occ,
                'target_footnote_occurrences': seg_tgt_occ
            })
            i = s_list[-1] + 1
            j = t_list[-1] + 1

        # Flush any remaining unmatched sentences
        flush_unmatched()

        return [[seg] for seg in segments]

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