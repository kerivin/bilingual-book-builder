import re
from typing import List, Tuple, Set
from bs4 import BeautifulSoup, Comment, Declaration, Doctype, NavigableString, ProcessingInstruction, Tag

from bbb.splitter import Splitter
from bbb.constants import BLOCK_TAGS, HEADING_TAGS
from lingua import Language

BR_PLACEHOLDER = '__BR__'
BR_TAG = '<br/>'


def _leaf_blocks(container) -> List[Tag]:
    if not isinstance(container, Tag):
        return [container]
    blocks = [b for b in container.find_all(BLOCK_TAGS)
              if not any(isinstance(c, Tag) and c.name in BLOCK_TAGS for c in b.children)]
    return blocks or [container]


class HtmlSentenceTokenizer:
    def __init__(self, splitter: Splitter):
        self.splitter = splitter

    def extract(self, html: str, language: Language) -> Tuple[List[Tuple[str, str]], Set[int]]:
        soup = BeautifulSoup(html, 'html.parser')
        self._remove_blacklisted(soup)

        root = soup
        if (len(soup.contents) == 1
                and hasattr(soup.contents[0], 'name')
                and soup.contents[0].name == '[document]'):
            root = soup.contents[0]

        self._replace_br_with_placeholder(root)
        self._join_soft_wrapped_lines(root)

        body = root.body if hasattr(root, 'body') and root.body else root
        block_elements = _leaf_blocks(body) if isinstance(body, Tag) else [root]

        all_sents = []
        paragraph_starts = set()

        for block in block_elements:
            block_text = block.get_text()
            norm_text, norm_to_raw = self._normalize_and_map(block_text)

            if not norm_text.strip():
                continue

            block_start_idx = len(all_sents)
            block_sents = []
            cursor = 0
            for line in norm_text.split('\n'):
                line_begin = cursor
                line_end = cursor + len(line)
                cursor = line_end + 1
                stripped = line.strip()
                if not stripped:
                    continue
                search_from = line_begin
                line_groups = self.splitter.run(stripped, language)
                for group in line_groups:
                    for sent in group:
                        s = sent.strip()
                        if not s:
                            continue
                        pos = self._find_sentence_position(norm_text, s, search_from, line_end)
                        if pos is None:
                            continue
                        end = pos + len(s)
                        search_from = end
                        raw_start = norm_to_raw[pos] if pos < len(norm_to_raw) else 0
                        raw_end = norm_to_raw[end] if end < len(norm_to_raw) else len(block_text)
                        block_sents.append((s, raw_start, raw_end))

            if not block_sents:
                continue

            if block.name not in HEADING_TAGS:
                paragraph_starts.add(block_start_idx)

            fragments = self._extract_fragments(block, [(a, b) for _, a, b in block_sents])
            for i, (sent, _, _) in enumerate(block_sents):
                all_sents.append((sent, fragments[i].replace(BR_PLACEHOLDER, BR_TAG)))

        if not all_sents:
            full_text = root.get_text()
            if full_text.strip():
                return [(full_text.strip(), str(root))], {0}
            return [], set()

        return all_sents, paragraph_starts

    @staticmethod
    def _find_sentence_position(text: str, sent: str, search_start: int, search_end: int) -> int:
        pos = text.find(sent, search_start, search_end)
        if pos != -1:
            return pos
        stripped = re.sub(r'\s+', ' ', sent).strip()
        pos = text.find(stripped, search_start, search_end)
        if pos != -1:
            return pos
        pattern = re.sub(r'\\ ', r'\\s+', re.escape(sent))
        match = re.search(pattern, text[search_start:search_end])
        if match:
            return search_start + match.start()
        return None

    def _remove_blacklisted(self, soup: BeautifulSoup) -> None:
        for tag in soup(['script', 'style', 'img', 'figure', 'svg', 'canvas']):
            tag.decompose()

    def _replace_br_with_placeholder(self, root: Tag) -> None:
        for br in root.find_all('br'):
            br.replace_with(NavigableString(BR_PLACEHOLDER))

    def _join_soft_wrapped_lines(self, root: Tag) -> None:
        def _looks_wrapped(text: str) -> bool:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            hyphenated_break = re.search(r'\w-\s*\n\s*\w', text)
            long_wrapped_lines = len(lines) >= 3 and sum(len(line) >= 40 for line in lines) >= len(lines) - 1
            return bool(hyphenated_break) or bool(long_wrapped_lines)

        for block in _leaf_blocks(root):
            block_wrapped = _looks_wrapped(block.get_text())
            for text_node in block.find_all(string=True):
                text = str(text_node)
                if not (block_wrapped or _looks_wrapped(text)):
                    continue
                text = re.sub(r'(\w)-\s*\n\s*(\w)', r'\1\2', text)
                text = re.sub(r'\s*\n\s*', ' ', text)
                if text != str(text_node):
                    text_node.replace_with(NavigableString(text))

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

    def _extract_fragments(self, root: Tag, intervals: List[Tuple[int, int]]) -> List[str]:
        ranges = {}
        self._compute_ranges(root, 0, ranges)

        builder = BeautifulSoup('', 'html.parser')
        stacks = []
        for start, end in intervals:
            top = builder.new_tag(root.name, attrs=dict(root.attrs) if root.attrs else None)
            stacks.append([top])

        self._collect_fragments(root, intervals, ranges, stacks, builder)
        return [str(stack[0]) for stack in stacks]

    @staticmethod
    def _compute_ranges(node, pos: int, ranges: dict) -> int:
        if isinstance(node, (Comment, ProcessingInstruction, Doctype, Declaration)):
            ranges[id(node)] = (pos, pos)
            return pos
        if isinstance(node, NavigableString):
            length = len(str(node))
            ranges[id(node)] = (pos, pos + length)
            return pos + length
        if not isinstance(node, Tag):
            ranges[id(node)] = (pos, pos)
            return pos

        p = pos
        starts = []
        ends = []
        for child in node.children:
            child_end = HtmlSentenceTokenizer._compute_ranges(child, p, ranges)
            start, _ = ranges[id(child)]
            starts.append(start)
            ends.append(child_end)
            p = child_end
        ranges[id(node)] = (min(starts, default=pos), max(ends, default=pos))
        return p

    def _collect_fragments(self, node: Tag, intervals, ranges, stacks, builder) -> None:
        for child in node.children:
            if isinstance(child, (Comment, ProcessingInstruction, Doctype, Declaration)):
                continue
            if isinstance(child, NavigableString):
                text = str(child)
                cstart, cend = ranges[id(child)]
                for idx, (istart, iend) in enumerate(intervals):
                    if cend <= istart or cstart >= iend:
                        continue
                    stack = stacks[idx]
                    if cstart >= istart and cend <= iend:
                        stack[-1].append(text)
                    else:
                        lo = max(cstart, istart)
                        hi = min(cend, iend)
                        stack[-1].append(text[lo - cstart:hi - cstart])
            elif isinstance(child, Tag):
                cstart, cend = ranges[id(child)]
                new_tags = {}
                for idx, (istart, iend) in enumerate(intervals):
                    if cend <= istart or cstart >= iend:
                        continue
                    new_tag = builder.new_tag(child.name, attrs=dict(child.attrs) if child.attrs else None)
                    stacks[idx][-1].append(new_tag)
                    stacks[idx].append(new_tag)
                    new_tags[idx] = new_tag
                if new_tags:
                    self._collect_fragments(child, intervals, ranges, stacks, builder)
                for idx, new_tag in new_tags.items():
                    stacks[idx].pop()
                    if not new_tag.contents:
                        new_tag.decompose()