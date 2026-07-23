import re
from typing import List, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag

from bbb.splitter import Splitter
from lingua import Language

BLOCK_TAGS = {'p', 'div', 'blockquote', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
              'section', 'article', 'header', 'footer'}


class HtmlSentenceTokenizer:
    def __init__(self, splitter: Splitter):
        self.splitter = splitter

    def extract(self, html: str, language: Language) -> Tuple[List[Tuple[str, str]], List[int]]:
        soup = BeautifulSoup(html, 'html.parser')
        self._remove_blacklisted(soup)

        root = soup
        if (hasattr(soup, 'contents') and len(soup.contents) == 1
                and hasattr(soup.contents[0], 'name')
                and soup.contents[0].name == '[document]'):
            root = soup.contents[0]

        body = root.body if hasattr(root, 'body') and root.body else root

        # break blocks that contain <br> into separate blocks per line
        for block_elem in body.find_all(BLOCK_TAGS):
            if block_elem.find('br'):
                self._split_block_at_br(block_elem)

        flat_sentences = []
        block_lengths = []

        for block_elem in body.find_all(BLOCK_TAGS, recursive=False):
            block_text = block_elem.get_text()
            norm_text, norm_to_raw = self._normalize_and_map(block_text)

            if not norm_text.strip():
                continue

            paragraphs = self.splitter.run(norm_text, language)
            block_sentences = []

            for para_sents in paragraphs:
                for sent in para_sents:
                    s = sent.strip()
                    if not s:
                        continue

                    pos = norm_text.find(s)
                    if pos == -1:
                        stripped = re.sub(r'\s+', ' ', s).strip()
                        pos = norm_text.find(stripped)
                    if pos == -1:
                        pattern = re.escape(s)
                        pattern = re.sub(r'\\ ', r'\\s+', pattern)
                        match = re.search(pattern, norm_text)
                        if match:
                            pos = match.start()
                        else:
                            continue

                    end = pos + len(s)
                    raw_start = norm_to_raw[pos] if pos < len(norm_to_raw) else 0
                    raw_end = norm_to_raw[end] if end < len(norm_to_raw) else len(block_text)

                    inner_fragment = self._extract_fragment(block_elem, raw_start, raw_end)

                    wrapper = BeautifulSoup('', 'html.parser').new_tag(
                        block_elem.name,
                        attrs=dict(block_elem.attrs) if block_elem.attrs else None
                    )
                    wrapper.append(BeautifulSoup(inner_fragment, 'html.parser'))
                    block_sentences.append((s, str(wrapper)))

            if block_sentences:
                flat_sentences.extend(block_sentences)
                block_lengths.append(len(block_sentences))

        return flat_sentences, block_lengths

    def _split_block_at_br(self, block_elem: Tag) -> None:
        """Replace a block element containing <br> with separate sibling blocks, one per br-separated segment."""
        # Collect all children, splitting at <br> into groups
        segments = []          # list of lists of nodes for each segment
        current_segment = []
        for child in block_elem.children:
            if isinstance(child, Tag) and child.name == 'br':
                if current_segment:
                    segments.append(current_segment)
                    current_segment = []
            else:
                current_segment.append(child)
        if current_segment:
            segments.append(current_segment)

        if len(segments) <= 1:
            return   # no <br> or only one segment, nothing to split

        parent = block_elem.parent
        # Create new blocks for each segment and insert them before the old block
        for seg_nodes in segments:
            new_block = BeautifulSoup('', 'html.parser').new_tag(
                block_elem.name,
                attrs=dict(block_elem.attrs) if block_elem.attrs else None
            )
            for node in seg_nodes:
                new_block.append(node)
            block_elem.insert_before(new_block)
        block_elem.decompose()

    def _remove_blacklisted(self, soup: BeautifulSoup) -> None:
        for tag in soup(['script', 'style', 'img', 'figure', 'svg', 'canvas']):
            tag.decompose()

    def _normalize_and_map(self, raw_text: str) -> Tuple[str, List[int]]:
        norm_parts = []
        norm_to_raw = []
        i_raw = 0
        n_raw = len(raw_text)

        while i_raw < n_raw:
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