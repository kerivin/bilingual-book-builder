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

    def extract(self, html: str, language: Language) -> List[Tuple[str, str]]:
        soup = BeautifulSoup(html, 'html.parser')
        self._remove_blacklisted(soup)

        root = soup
        if (hasattr(soup, 'contents') and len(soup.contents) == 1
                and hasattr(soup.contents[0], 'name')
                and soup.contents[0].name == '[document]'):
            root = soup.contents[0]

        self._replace_br_with_placeholder(root)

        full_text = root.get_text()
        norm_text, norm_to_raw = self._normalize_and_map(full_text)

        if not norm_text.strip():
            return []

        paragraph_groups = self.splitter.run(norm_text, language)
        if not paragraph_groups or not paragraph_groups[0]:
            return [(norm_text.strip(), str(root))]

        all_sents = [s for para in paragraph_groups for s in para if s.strip()]
        if not all_sents:
            return [(norm_text.strip(), str(root))]

        results = []
        last_pos = 0
        for sent in all_sents:
            pos = norm_text.find(sent, last_pos)
            if pos == -1:
                stripped = re.sub(r'\s+', ' ', sent).strip()
                pos = norm_text.find(stripped, last_pos)
            if pos == -1:
                pattern = re.escape(sent)
                pattern = re.sub(r'\\ ', r'\\s+', pattern)
                match = re.search(pattern, norm_text[last_pos:])
                if match:
                    pos = last_pos + match.start()
                else:
                    continue
            end = pos + len(sent)
            last_pos = end

            raw_start = norm_to_raw[pos] if pos < len(norm_to_raw) else 0
            raw_end = norm_to_raw[end] if end < len(norm_to_raw) else len(full_text)
            fragment = self._extract_fragment(root, raw_start, raw_end)
            fragment = fragment.replace(BR_PLACEHOLDER, BR_TAG)
            results.append((sent, fragment))

        return results

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