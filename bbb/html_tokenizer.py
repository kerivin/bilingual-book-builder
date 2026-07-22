import re
from typing import List, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag

from bbb.splitter import Splitter
from lingua import Language


BR_PLACEHOLDER = '__BR__'
BR_TAG = '<br/>'


class HtmlSentenceTokenizer:
    def __init__(self, splitter: Splitter):
        self.splitter = splitter

    def extract(self, html: str, language: Language) -> Tuple[List[Tuple[str, str]], List[bool]]:
        soup = BeautifulSoup(html, 'html.parser')
        self._remove_blacklisted(soup)

        root = soup
        if (hasattr(soup, 'contents') and len(soup.contents) == 1
                and hasattr(soup.contents[0], 'name')
                and soup.contents[0].name == '[document]'):
            root = soup.contents[0]

        self._replace_br_with_placeholder(root)

        flat_text = root.get_text()
        norm_flat, norm_to_raw = self._normalize_and_map(flat_text)

        if not norm_flat.strip():
            return [], []

        para_text = root.get_text(separator='\n')
        norm_para = self._normalize_for_paragraphs(para_text)

        groups_flat = self.splitter.run(norm_flat, language)
        if not groups_flat or not groups_flat[0]:
            return [(norm_flat.strip(), str(root))], [True]

        flat_sents = [s for para in groups_flat for s in para if s.strip()]
        if not flat_sents:
            return [(norm_flat.strip(), str(root))], [True]

        sent_positions = []
        last_pos = 0
        for sent in flat_sents:
            pos = norm_flat.find(sent, last_pos)
            if pos == -1:
                stripped = re.sub(r'\s+', ' ', sent).strip()
                pos = norm_flat.find(stripped, last_pos)
            if pos == -1:
                pattern = re.escape(sent)
                pattern = re.sub(r'\\ ', r'\\s+', pattern)
                match = re.search(pattern, norm_flat[last_pos:])
                if match:
                    pos = last_pos + match.start()
                else:
                    continue
            end = pos + len(sent)
            last_pos = end
            sent_positions.append((pos, end))

        para_groups = self.splitter.run(norm_para, language)
        para_starts = [False] * len(flat_sents)

        if para_groups and para_groups[0]:
            next_flat_idx = 0
            for group in para_groups:
                first = group[0].strip() if group else ''
                if not first:
                    continue
                for idx in range(next_flat_idx, len(flat_sents)):
                    if flat_sents[idx].strip() == first:
                        para_starts[idx] = True
                        next_flat_idx = idx + 1
                        break

        results = []
        for i, sent in enumerate(flat_sents):
            pos = sent_positions[i][0]
            end = sent_positions[i][1]
            raw_start = norm_to_raw[pos] if pos < len(norm_to_raw) else 0
            raw_end = norm_to_raw[end] if end < len(norm_to_raw) else len(flat_text)
            fragment = self._extract_fragment(root, raw_start, raw_end)
            fragment = fragment.replace(BR_PLACEHOLDER, BR_TAG)
            results.append((sent, fragment))

        return results, para_starts

    def _normalize_for_paragraphs(self, text: str) -> str:
        text = re.sub(r'[^\S\n]+', ' ', text)
        text = re.sub(r' *\n *', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _remove_blacklisted(self, soup: BeautifulSoup) -> None:
        for tag in soup(['script', 'style', 'img', 'figure', 'svg', 'canvas']):
            tag.decompose()

    def _replace_br_with_placeholder(self, root: Tag) -> None:
        for br in root.find_all('br'):
            br.replace_with(NavigableString(BR_PLACEHOLDER))

    def _normalize_and_map(self, raw_text: str) -> Tuple[str, List[int]]:
        norm_parts = []
        norm_to_raw = []
        i_raw = 0
        n_raw = len(raw_text)

        while i_raw < n_raw:
            if raw_text[i_raw:i_raw + len(BR_PLACEHOLDER)] == BR_PLACEHOLDER:
                norm_parts.append('\n')
                norm_to_raw.append(i_raw)
                i_raw += len(BR_PLACEHOLDER)
                continue

            if raw_text[i_raw] == '\n':
                norm_parts.append('\n')
                norm_to_raw.append(i_raw)
                i_raw += 1
                continue

            if raw_text[i_raw].isspace() and raw_text[i_raw] != '\n':
                start_ws = i_raw
                while i_raw < n_raw and raw_text[i_raw].isspace() and raw_text[i_raw] != '\n':
                    i_raw += 1
                if norm_parts and norm_parts[-1] not in (' ', '\n'):
                    norm_parts.append(' ')
                    norm_to_raw.append(start_ws)
                continue

            norm_parts.append(raw_text[i_raw])
            norm_to_raw.append(i_raw)
            i_raw += 1

        return ''.join(norm_parts), norm_to_raw

    def _extract_fragment(self, root: Tag, start: int, end: int) -> str:
        wrapper = BeautifulSoup('', 'html.parser').new_tag('div')
        self._collect_fragment(root, wrapper, 0, start, end)
        return wrapper.decode_contents()

    def _collect_fragment(self, src_node, dest_parent, current_pos, start, end) -> int:
        if isinstance(src_node, NavigableString):
            text = str(src_node)
            node_len = len(text)
            node_start = current_pos
            node_end = current_pos + node_len

            if node_end <= start or node_start >= end:
                return current_pos + node_len

            if node_start >= start and node_end <= end:
                dest_parent.append(text)
            else:
                if node_start < start and node_end > start:
                    text = text[start - node_start:]
                    node_start = start
                if node_end > end and node_start < end:
                    text = text[:end - node_end]
                dest_parent.append(text)

            return current_pos + node_len

        if not isinstance(src_node, Tag):
            return current_pos

        for child in src_node.children:
            if isinstance(child, NavigableString):
                current_pos = self._collect_fragment(child, dest_parent, current_pos, start, end)
            elif isinstance(child, Tag):
                new_tag = BeautifulSoup('', 'html.parser').new_tag(
                    child.name, attrs=dict(child.attrs) if child.attrs else None
                )
                dest_parent.append(new_tag)
                current_pos = self._collect_fragment(child, new_tag, current_pos, start, end)
                if not new_tag.contents:
                    new_tag.decompose()
        return current_pos