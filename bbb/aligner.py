import re
import numpy as np
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

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

    @staticmethod
    def _para_plain(tokens):
        parts = [t.content for t in tokens if t.kind == 'text']
        raw = ''.join(parts).replace('__BR__', '\n')
        raw = re.sub(r'[^\S\n]+', ' ', raw)
        raw = re.sub(r' *\n *', '\n', raw)
        return raw.strip()

    @staticmethod
    def _map_offsets(tokens, norm_text):
        pieces = []
        for t in tokens:
            if t.kind != 'text':
                continue
            orig = t.content
            norm = orig.replace('__BR__', '\n')
            norm = re.sub(r'[^\S\n]+', ' ', norm)
            norm = re.sub(r' *\n *', '\n', norm)
            pieces.append((orig, norm))

        full_norm = ''.join(p[1] for p in pieces)
        lstrip = len(full_norm) - len(full_norm.lstrip())
        rstrip = len(full_norm) - len(full_norm.rstrip())

        mapping = []
        orig_pos = 0
        for orig, norm in pieces:
            o_idx = 0
            n_idx = 0
            while n_idx < len(norm):
                mapping.append(orig_pos + o_idx)
                if norm[n_idx] == '\n' and orig[o_idx:o_idx+5] == '__BR__':
                    o_idx += 5
                    n_idx += 1
                else:
                    o_idx += 1
                    n_idx += 1
            orig_pos += len(orig)

        mapping = mapping[lstrip: len(mapping) - rstrip]
        mapping.append(orig_pos)
        return mapping

    def _sentence_html(self, tokens, norm_start, norm_end, mapping):
        if norm_start >= norm_end or not mapping:
            return ''
        orig_start = mapping[norm_start]
        orig_end = mapping[norm_end] if norm_end < len(mapping) else mapping[-1]
        html, _ = rebuild_sentence(tokens, orig_start, orig_end)
        return html.replace('__BR__', '<br/>')

    def _align_pair(self, source_text: str, target_text: str,
                    src_footnote_refs=None, tgt_footnote_refs=None,
                    src_tokens=None, tgt_tokens=None) -> List[Dict[str, Any]]:
        if not source_text.strip() or not target_text.strip():
            return [[{'source': source_text, 'target': target_text,
                      'source_html': source_text, 'target_html': target_text}]]

        src_lang = self._detect_language(source_text, self.source_language)
        tgt_lang = self._detect_language(target_text, self.target_language)

        token_pattern = re.compile(rf'({re.escape(SRC_FN_PREFIX)}FNREF_\d+|{re.escape(TGT_FN_PREFIX)}FNREF_\d+)')
        fn_lookup = {}
        for fn in (src_footnote_refs or []):
            fn_lookup[fn['token']] = fn['target_id']
        for fn in (tgt_footnote_refs or []):
            fn_lookup[fn['token']] = fn['target_id']

        def process_side(full_text, token_lists, lang):
            para_norms = [self._para_plain(tokens) for tokens in token_lists]
            mappings = [self._map_offsets(tokens, norm) for tokens, norm in zip(token_lists, para_norms)]

            all_sents = []
            sent_info = []
            for para_idx, norm_text in enumerate(para_norms):
                if not norm_text:
                    continue
                sents = self.splitter.run(norm_text, lang)
                if not sents:
                    sents = [[norm_text]]
                search_from = 0
                for sent in sents[0]:
                    pos = norm_text.find(sent, search_from)
                    if pos == -1:
                        sent_clean = re.sub(r'\s+', ' ', sent).strip()
                        pos = norm_text.find(sent_clean, search_from)
                    if pos != -1:
                        end_pos = pos + len(sent)
                        all_sents.append(sent)
                        sent_info.append((para_idx, pos, end_pos))
                        search_from = end_pos
                    else:
                        all_sents.append(sent)
                        sent_info.append((para_idx, 0, 0))

            clean_sents = []
            for s in all_sents:
                c = token_pattern.sub(' ', s)
                c = re.sub(r'\s+', ' ', c).strip()
                clean_sents.append(c)

            return clean_sents, all_sents, sent_info, token_lists, mappings

        src_clean, src_sents, src_info, src_tokens, src_maps = process_side(
            source_text, src_tokens, src_lang)
        tgt_clean, tgt_sents, tgt_info, tgt_tokens, tgt_maps = process_side(
            target_text, tgt_tokens, tgt_lang)

        if not src_clean or not tgt_clean:
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
            max_len = max(len(src_clean), len(tgt_clean))
            raw_pairs = [([i] if i < len(src_clean) else [], [i] if i < len(tgt_clean) else [])
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
        S, T = len(src_clean), len(tgt_clean)

        unmatched_src_buf = []
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
            if i < S and i not in matched_src:
                para_src, s_start, s_end = src_info[i]
                html = self._sentence_html(src_tokens[para_src], s_start, s_end, src_maps[para_src]) if s_end > s_start else src_sents[i]
                occs = [{'token': m.group(1), 'target_id': fn_lookup[m.group(1)]}
                        for m in token_pattern.finditer(src_sents[i]) if m.group(1) in fn_lookup]
                unmatched_src_buf.append((src_sents[i], html, occs))
                i += 1
                continue
            if j < T and j not in matched_tgt:
                para_tgt, t_start, t_end = tgt_info[j]
                html = self._sentence_html(tgt_tokens[para_tgt], t_start, t_end, tgt_maps[para_tgt]) if t_end > t_start else tgt_sents[j]
                occs = [{'token': m.group(1), 'target_id': fn_lookup[m.group(1)]}
                        for m in token_pattern.finditer(tgt_sents[j]) if m.group(1) in fn_lookup]
                unmatched_tgt_buf.append((tgt_sents[j], html, occs))
                j += 1
                continue

            flush_unmatched()

            pair = find_pair_with_src(i) or find_pair_with_tgt(j)
            if pair is None:
                if i < S:
                    para_src, s_start, s_end = src_info[i]
                    html = self._sentence_html(src_tokens[para_src], s_start, s_end, src_maps[para_src]) if s_end > s_start else src_sents[i]
                    occs = [{'token': m.group(1), 'target_id': fn_lookup[m.group(1)]}
                            for m in token_pattern.finditer(src_sents[i]) if m.group(1) in fn_lookup]
                    unmatched_src_buf.append((src_sents[i], html, occs))
                    i += 1
                if j < T:
                    para_tgt, t_start, t_end = tgt_info[j]
                    html = self._sentence_html(tgt_tokens[para_tgt], t_start, t_end, tgt_maps[para_tgt]) if t_end > t_start else tgt_sents[j]
                    occs = [{'token': m.group(1), 'target_id': fn_lookup[m.group(1)]}
                            for m in token_pattern.finditer(tgt_sents[j]) if m.group(1) in fn_lookup]
                    unmatched_tgt_buf.append((tgt_sents[j], html, occs))
                    j += 1
                continue

            s_list, t_list = pair
            seg_src = '\n'.join(src_sents[idx] for idx in s_list)
            seg_tgt = '\n'.join(tgt_sents[idx] for idx in t_list)
            seg_src_html = '\n'.join(
                self._sentence_html(src_tokens[src_info[idx][0]], src_info[idx][1], src_info[idx][2], src_maps[src_info[idx][0]])
                if src_info[idx][2] > src_info[idx][1] else src_sents[idx]
                for idx in s_list
            )
            seg_tgt_html = '\n'.join(
                self._sentence_html(tgt_tokens[tgt_info[idx][0]], tgt_info[idx][1], tgt_info[idx][2], tgt_maps[tgt_info[idx][0]])
                if tgt_info[idx][2] > tgt_info[idx][1] else tgt_sents[idx]
                for idx in t_list
            )
            seg_src_occ = [
                o for idx in s_list
                for m in token_pattern.finditer(src_sents[idx]) if m.group(1) in fn_lookup
                for o in [{'token': m.group(1), 'target_id': fn_lookup[m.group(1)]}]
            ]
            seg_tgt_occ = [
                o for idx in t_list
                for m in token_pattern.finditer(tgt_sents[idx]) if m.group(1) in fn_lookup
                for o in [{'token': m.group(1), 'target_id': fn_lookup[m.group(1)]}]
            ]
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