import numpy as np
from enum import IntEnum
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from itertools import accumulate
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

        def process_side(token_list, prefix, fn_refs, lang):
            para_sentences = []
            para_cleans = []
            para_htmls = []
            para_occs = []

            token_pattern = re.compile(rf'\s*{re.escape(prefix)}FNREF_(\d+)\s*')
            fn_lookup = {}
            for fn in (fn_refs or []):
                fn_lookup[fn['token']] = fn['target_id']

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
                s_list, c_list, h_list, o_list = [], [], [], []
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

                    s_list.append(sent)
                    c_list.append(clean)
                    o_list.append(occ)
                    html, _ = rebuild_sentence(para_tokens, start, end)
                    h_list.append(html)

                para_sentences.append(s_list)
                para_cleans.append(c_list)
                para_htmls.append(h_list)
                para_occs.append(o_list)

            flat_s = [s for p in para_sentences for s in p]
            flat_c = [s for p in para_cleans for s in p]
            flat_h = [h for p in para_htmls for h in p]
            flat_o = [o for p in para_occs for o in p]
            para_bounds = [0] + list(accumulate(len(p) for p in para_sentences))
            return (para_sentences, para_cleans, para_htmls, para_occs,
                    flat_s, flat_c, flat_h, flat_o, para_bounds)

        (src_paras, src_paracleans, src_parahtmls, src_paraoccs,
         src_flat, src_clean, src_htmls, src_occs, src_bounds) = process_side(
            src_tokens, SRC_FN_PREFIX, src_footnote_refs, src_lang)
        (tgt_paras, tgt_paracleans, tgt_parahtmls, tgt_paraoccs,
         tgt_flat, tgt_clean, tgt_htmls, tgt_occs, tgt_bounds) = process_side(
            tgt_tokens, TGT_FN_PREFIX, tgt_footnote_refs, tgt_lang)

        if not src_flat or not tgt_flat:
            return [[{'source': source_text, 'target': target_text,
                      'source_html': source_text, 'target_html': target_text}]]

        def embed_sents(sentences):
            return self.align_model_encoder.model.encode(sentences, convert_to_numpy=True, show_progress_bar=False)

        src_sent_embs = embed_sents(src_clean)
        tgt_sent_embs = embed_sents(tgt_clean)

        def paragraph_embeddings(paras, bounds, sent_embs):
            embs = []
            for i in range(len(paras)):
                start, end = bounds[i], bounds[i+1]
                if start == end:
                    embs.append(np.zeros(sent_embs.shape[1]))
                else:
                    embs.append(np.mean(sent_embs[start:end], axis=0))
            return np.array(embs)

        src_para_embs = paragraph_embeddings(src_paras, src_bounds, src_sent_embs)
        tgt_para_embs = paragraph_embeddings(tgt_paras, tgt_bounds, tgt_sent_embs)
        src_para_embs = src_para_embs / (np.linalg.norm(src_para_embs, axis=1, keepdims=True) + 1e-9)
        tgt_para_embs = tgt_para_embs / (np.linalg.norm(tgt_para_embs, axis=1, keepdims=True) + 1e-9)

        S, T = len(src_paras), len(tgt_paras)
        sim = np.dot(src_para_embs, tgt_para_embs.T)

        PARA_THRESHOLD = 0.5

        dp = np.full((S+1, T+1), -1e9)
        dp[0, 0] = 0.0
        class Dir(IntEnum):
            DIAG = 0; UP = 1; LEFT = 2
        back = np.zeros((S+1, T+1), dtype=int)
        for i in range(S+1):
            for j in range(T+1):
                if i == 0 and j == 0: continue
                best, best_ptr = -1e9, None
                if i > 0 and j > 0 and sim[i-1, j-1] >= PARA_THRESHOLD:
                    score = dp[i-1, j-1] + sim[i-1, j-1]
                    if score > best:
                        best, best_ptr = score, Dir.DIAG
                if i > 0:
                    score = dp[i-1, j]
                    if score > best:
                        best, best_ptr = score, Dir.UP
                if j > 0:
                    score = dp[i, j-1]
                    if score > best:
                        best, best_ptr = score, Dir.LEFT
                dp[i, j] = best
                back[i, j] = best_ptr

        para_pairs = []
        i, j = S, T
        while i > 0 or j > 0:
            if back[i, j] == Dir.DIAG:
                i -= 1; j -= 1
                para_pairs.append((i, j))
            elif back[i, j] == Dir.UP:
                i -= 1
                para_pairs.append((i, None))
            else:
                j -= 1
                para_pairs.append((None, j))
        para_pairs.reverse()

        aligned_paras = []

        for src_p, tgt_p in para_pairs:
            if src_p is not None and tgt_p is not None:
                s_start, s_end = src_bounds[src_p], src_bounds[src_p+1]
                t_start, t_end = tgt_bounds[tgt_p], tgt_bounds[tgt_p+1]
                if s_start == s_end and t_start == t_end:
                    continue
                try:
                    bert = Bertalign(
                        model_encoder=self.align_model_encoder,
                        source_sentences=src_clean[s_start:s_end],
                        target_sentences=tgt_clean[t_start:t_end],
                    )
                    bert.align_sents()
                    result = bert.result
                except Exception as e:
                    self.log.warning(f"Bertalign failed on paragraph pair: {e}")
                    result = []
                if not result:
                    s_indices = list(range(s_start, s_end))
                    t_indices = list(range(t_start, t_end))
                    result = [(s_indices, t_indices)]

                para_segments = []
                for s_idx_list, t_idx_list in result:
                    s_global = [i + s_start for i in s_idx_list] if s_idx_list else []
                    t_global = [i + t_start for i in t_idx_list] if t_idx_list else []

                    seg_src = '\n'.join(src_flat[i] for i in s_global) if s_global else ''
                    seg_tgt = '\n'.join(tgt_flat[i] for i in t_global) if t_global else ''
                    seg_src_html = '\n'.join(src_htmls[i] for i in s_global) if s_global else ''
                    seg_tgt_html = '\n'.join(tgt_htmls[i] for i in t_global) if t_global else ''
                    seg_src_occ = []
                    for i in s_global: seg_src_occ.extend(src_occs[i])
                    seg_tgt_occ = []
                    for i in t_global: seg_tgt_occ.extend(tgt_occs[i])
                    para_segments.append({
                        'source': seg_src,
                        'target': seg_tgt,
                        'source_html': seg_src_html,
                        'target_html': seg_tgt_html,
                        'source_footnote_occurrences': seg_src_occ,
                        'target_footnote_occurrences': seg_tgt_occ
                    })
                aligned_paras.append(para_segments)

            elif src_p is not None:
                para_segments = []
                for i in range(src_bounds[src_p], src_bounds[src_p+1]):
                    para_segments.append({
                        'source': src_flat[i],
                        'target': '',
                        'source_html': src_htmls[i],
                        'target_html': '',
                        'source_footnote_occurrences': src_occs[i],
                        'target_footnote_occurrences': []
                    })
                aligned_paras.append(para_segments)

            else:
                para_segments = []
                for i in range(tgt_bounds[tgt_p], tgt_bounds[tgt_p+1]):
                    para_segments.append({
                        'source': '',
                        'target': tgt_flat[i],
                        'source_html': '',
                        'target_html': tgt_htmls[i],
                        'source_footnote_occurrences': [],
                        'target_footnote_occurrences': tgt_occs[i]
                    })
                aligned_paras.append(para_segments)

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