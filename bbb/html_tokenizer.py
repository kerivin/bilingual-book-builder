import re
from typing import List, Tuple, Set
from bs4 import BeautifulSoup, NavigableString, Tag

from bbb.splitter import Splitter
from lingua import Language

BR_PLACEHOLDER = '__BR__'
BR_TAG = '<br/>'

BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'li',
              'section', 'article', 'header', 'footer', 'aside', 'main'}
HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}


class HtmlSentenceTokenizer:
    def __init__(self, splitter: Splitter):
        self.splitter = splitter

    def extract(self, html: str, language: Language) -> Tuple[List[Tuple[str, str]], Set[int]]:
        soup = BeautifulSoup(html, 'html.parser')
        self._remove_blacklisted(soup)

        root = soup
        if (hasattr(soup, 'contents') and len(soup.contents) == 1
                and hasattr(soup.contents[0], 'name')
                and soup.contents[0].name == '[document]'):
            root = soup.contents[0]

        self._replace_br_with_placeholder(root)

        # Find all block elements in the document
        body = root.body if hasattr(root, 'body') and root.body else root
        all_blocks = body.find_all(BLOCK_TAGS) if isinstance(body, Tag) else []
        
        # Filter to only the deepest blocks (those that don't contain other block elements as direct children)
        block_elements = []
        for block in all_blocks:
            if isinstance(block, Tag):
                # Check if this block has any direct children that are block elements
                has_block_children = any(
                    child.name in BLOCK_TAGS 
                    for child in block.children 
                    if isinstance(child, Tag)
                )
                if not has_block_children:
                    block_elements.append(block)
        
        # If no block elements found, use root itself
        if not block_elements:
            block_elements = [root] if isinstance(root, Tag) else [soup]

        all_sents = []
        paragraph_starts = set()

        for block in block_elements:
            block_text = block.get_text()
            norm_text, norm_to_raw = self._normalize_and_map(block_text)

            if not norm_text.strip():
                continue

            block_start_idx = len(all_sents)
            # Split by newlines to pre-split sentences separated by line breaks
            lines = norm_text.split('\n')
            block_sents = []

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                line_groups = self.splitter.run(line, language)
                for group in line_groups:
                    for sent in group:
                        s = sent.strip()
                        if s:
                            block_sents.append(s)

            if not block_sents:
                continue

            # Only the first sentence of a block is a paragraph start;
            # newline-separated sentences belong to the same paragraph.
            block_tag_name = block.name if hasattr(block, 'name') else None
            if block_tag_name not in HEADING_TAGS:
                paragraph_starts.add(block_start_idx)

            # Extract HTML fragments for each sentence
            last_pos = 0
            for sent in block_sents:
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
                raw_end = norm_to_raw[end] if end < len(norm_to_raw) else len(block_text)
                fragment = self._extract_fragment(block, raw_start, raw_end)
                fragment = fragment.replace(BR_PLACEHOLDER, BR_TAG)
                all_sents.append((sent, fragment))

        if not all_sents:
            full_text = root.get_text()
            return [(full_text.strip(), str(root))], {0}

        return all_sents, paragraph_starts

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
        # Create a copy of the root tag to preserve its attributes
        root_copy = BeautifulSoup('', 'html.parser').new_tag(
            root.name, attrs=dict(root.attrs) if root.attrs else None
        )
        self._collect_fragment(root, root_copy, 0, start, end)
        return str(root_copy)

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
                    text = text[:end - node_start]
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