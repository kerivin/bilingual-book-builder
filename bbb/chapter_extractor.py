import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from bs4 import BeautifulSoup, NavigableString, Comment
from epub_utils import Document

from bbb import progress, utils

HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
BLOCK_TAGS = {'p', 'div', 'li', 'blockquote', *HEADING_TAGS}
SKIP_CLASSES = {'cn', 'chapnum', 'chapter-number'}
TAG_BLACKLIST = {'script', 'style', 'img', 'figure', 'svg', 'canvas'}


class ChapterExtractor:
    def __init__(self, path: str, force_show: bool = False,
                 preview_words: int = 20, min_chars: int = 200):
        self.doc = Document(path)
        self.force_show = force_show
        self.min_chars = min_chars
        self.preview_words = preview_words
        self.log = logging.getLogger(__name__)

        rootfile_path = self.doc.container.rootfile_path
        self._opf_base = str(Path(rootfile_path).parent) + "/" if Path(rootfile_path).parent != Path('.') else ""

        self._spine_full_hrefs: List[str] = []
        self._spine_idrefs: List[str] = []
        self._build_spine_info()

    def get_chapter_list(self) -> List[Dict[str, Any]]:
        chapters = self._extract_from_toc()
        if chapters and len(chapters) >= 2:
            return chapters
        return self._extract_via_headers()

    def _show_chapters(self, chapters: List[Dict[str, Any]]) -> None:
        if not self.force_show:
            return
        with utils.temporary_log_level(self.log, logging.INFO):
            title = self.doc.package.metadata.title
            self.log.info(f"\n{title}\n------")
            for ch in chapters:
                toc_label = ch['toc_path'][-1] if ch['toc_path'] else ""
                self.log.info(f"{toc_label}\n{ch['preview']}")
                utils.print_horizontal_line(self.log.info)

    def _create_chapter(self, display_path: List[str], toc_path: List[str],
                        full_text: str, item_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "display_path": display_path,
            "toc_path": toc_path,
            "full_text": full_text,
            "word_count": len(full_text.split()),
            "preview": " ".join(full_text.split()[:self.preview_words])
                       + ("…" if len(full_text.split()) > self.preview_words else ""),
            "item_id": item_id
        }

    def _build_spine_info(self) -> None:
        manifest_items = {item['id']: item['href'] for item in self.doc.package.manifest.items}
        for itemref in self.doc.package.spine.itemrefs:
            if itemref.get('linear') == 'no':
                continue
            href = manifest_items.get(itemref['idref'])
            if href is None:
                continue
            self._spine_full_hrefs.append(self._make_full_path(href))
            self._spine_idrefs.append(itemref['idref'])

    def _make_full_path(self, href: str) -> str:
        if href.startswith('/'):
            return href.lstrip('/')
        return f"{self._opf_base}{href}"

    def _find_guide_skip_indices(self) -> set:
        skip_types = {
            'cover', 'title-page', 'toc', 'copyright-page',
            'frontmatter', 'backmatter', 'acknowledgements',
            'other.frontmatter', 'other.backmatter'
        }
        skip_indices = set()
        for ref in getattr(self.doc.package, 'guide', []) or []:
            if ref.get('type', '').lower() in skip_types:
                idx = self._resolve_toc_target_to_spine_index(ref['href'])
                if idx is not None:
                    skip_indices.add(idx)
        return skip_indices

    def _is_skippable_frontbackmatter(self, soup: BeautifulSoup) -> bool:
        keep_types = {
            'dedication', 'foreword', 'preface', 'introduction', 'prologue',
            'epigraph', 'acknowledgments', 'afterword', 'conclusion',
            'part', 'chapter'
        }
        if not soup.body:
            return False

        for tag in soup.body.descendants:
            if not hasattr(tag, 'get'):
                continue
            etype = tag.get('epub:type', '')
            if not isinstance(etype, str):
                continue
            if any(t in etype.split() for t in keep_types):
                return False

        body_etype = soup.body.get('epub:type', '')
        if isinstance(body_etype, str) and ('frontmatter' in body_etype or 'backmatter' in body_etype):
            return True

        for child in soup.body.find_all(True, recursive=False):
            etype = child.get('epub:type', '')
            if isinstance(etype, str) and ('frontmatter' in etype or 'backmatter' in etype):
                return True
        return False

    def _resolve_toc_target_to_spine_index(self, target: str) -> Optional[int]:
        base = target.split('#')[0]
        candidates = [
            base,
            self._make_full_path(base),
            self._make_full_path(Path(base).name),
        ]
        for prefix in ('xhtml/', 'text/', 'OEBPS/', 'OEBPS/text/', 'OEBPS/xhtml/'):
            candidates.append(self._make_full_path(f"{prefix}{base}"))
            candidates.append(self._make_full_path(f"{prefix}{Path(base).name}"))

        seen = set()
        unique_candidates = [c for c in candidates if not (c in seen or seen.add(c))]
        for cand in unique_candidates:
            try:
                return self._spine_full_hrefs.index(cand)
            except ValueError:
                continue
        return None

    def _load_soup(self, full_href: str) -> Optional[BeautifulSoup]:
        try:
            content = self.doc.get_file_by_path(full_href)
            return BeautifulSoup(content.to_str(), "html.parser")
        except (ValueError, AttributeError, KeyError):
            return None

    def _clean_soup(self, soup: BeautifulSoup) -> None:
        for tag in soup(TAG_BLACKLIST):
            tag.decompose()
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()
        for tag in soup.find_all(class_=lambda c: c and set(c.split()) & SKIP_CLASSES):
            tag.decompose()

    def _clean_heading(self, raw: str) -> str:
        cleaned = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw)
        return cleaned or raw

    def _extract_heading_with_newlines(self, elem) -> str:
        if elem is None:
            return ""
        return elem.get_text(separator='\n').strip()

    def _extract_text(self, soup: BeautifulSoup, anchor_elements: Optional[Dict[str, Any]] = None, root_id: Optional[str] = None):
        self._clean_soup(soup)
        BR_PLACEHOLDER = '__BR__'
        PARA_PLACEHOLDER = '__PARA__'

        for br in soup.find_all('br'):
            br.replace_with(BR_PLACEHOLDER)

        root = soup.body if soup.body else soup

        if anchor_elements:
            anchor_texts = {aid: [] for aid in anchor_elements}
            current_anchor = None
            if root_id:
                anchor_texts[root_id] = []
                current_anchor = root_id
            else:
                current_anchor = None

            def walk(node):
                nonlocal current_anchor
                if isinstance(node, NavigableString):
                    text = str(node)
                    if current_anchor is not None:
                        anchor_texts[current_anchor].append(text)
                    return
                if not hasattr(node, 'name'):
                    return
                anchor_id = None
                if node.get('id') in anchor_elements:
                    anchor_id = node.get('id')
                elif root_id and node is anchor_elements.get(root_id):
                    anchor_id = root_id
                if anchor_id is not None:
                    current_anchor = anchor_id
                    if node.name in HEADING_TAGS:
                        return
                if node.name in BLOCK_TAGS:
                    if current_anchor is not None:
                        anchor_texts[current_anchor].append(PARA_PLACEHOLDER)
                for child in node.children:
                    walk(child)

            walk(root)

            result = {}
            for aid, pieces in anchor_texts.items():
                raw = ''.join(pieces)
                text = raw.replace(BR_PLACEHOLDER, '\n').replace(PARA_PLACEHOLDER, '\n\n')
                text = re.sub(r'[^\S\n]+', ' ', text)
                text = re.sub(r' *\n *', '\n', text)
                text = re.sub(r'\n{3,}', '\n\n', text)
                result[aid] = text.strip()
            return result

        else:
            parts = []
            def walk(node):
                if isinstance(node, NavigableString):
                    parts.append(str(node))
                    return
                if not hasattr(node, 'name'):
                    return
                if node.name in BLOCK_TAGS:
                    parts.append(PARA_PLACEHOLDER)
                for child in node.children:
                    walk(child)

            walk(root)
            raw = ''.join(parts)
            raw = raw.replace(BR_PLACEHOLDER, '\n').replace(PARA_PLACEHOLDER, '\n\n')
            text = re.sub(r'[^\S\n]+', ' ', raw)
            text = re.sub(r' *\n *', '\n', text)
            text = re.sub(r'\n{3,}', '\n\n', text)
            return text.strip()

    def _get_first_heading(self, full_href: str) -> Optional[str]:
        soup = self._load_soup(full_href)
        if not soup:
            return None
        h_tag = soup.find(HEADING_TAGS)
        if not h_tag:
            return None
        raw = h_tag.get_text(separator='\n').strip()
        return self._clean_heading(raw)

    def _extract_from_toc(self) -> List[Dict[str, Any]]:
        toc = self.doc.toc
        if not toc:
            return []

        def collect_with_path(nodes, path=()):
            for node in nodes:
                current_path = path + (node.label,)
                yield node, current_path
                if node.children:
                    yield from collect_with_path(node.children, current_path)

        skip_spine = self._find_guide_skip_indices()
        toc_entries = []
        seen_targets = set()
        for item, path in collect_with_path(toc.get_toc_items()):
            idx = self._resolve_toc_target_to_spine_index(item.target)
            if idx is None or idx in skip_spine:
                continue
            anchor = item.target.split('#', 1)[1] if '#' in item.target else None
            target_key = (idx, anchor)
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            toc_entries.append({
                "label": item.label,
                "toc_path_tuple": path,
                "spine_index": idx,
                "anchor": anchor,
                "full_href": self._spine_full_hrefs[idx],
            })
        if not toc_entries:
            return []

        # Build display titles for every prefix of toc paths
        path_display_titles = {}
        for entry in toc_entries:
            soup = self._load_soup(entry["full_href"])
            if not soup:
                continue
            self._clean_soup(soup)
            heading_text = entry["label"]
            if entry["anchor"] is not None:
                elem = soup.find(id=entry["anchor"])
                if elem is not None and elem.name in HEADING_TAGS:
                    heading_text = self._extract_heading_with_newlines(elem)
                elif elem is not None:
                    heading_text = self._extract_heading_with_newlines(elem)
            else:
                first_h = soup.find(HEADING_TAGS)
                if first_h:
                    heading_text = self._extract_heading_with_newlines(first_h)
            path_display_titles[entry["toc_path_tuple"]] = heading_text if heading_text else entry["label"]

        grouped: Dict[int, List[dict]] = {}
        order = []
        for e in toc_entries:
            idx = e["spine_index"]
            if idx not in grouped:
                grouped[idx] = []
                order.append(idx)
            grouped[idx].append(e)

        chapters = []
        ROOT_ID = "__root__"

        for idx in order:
            file_entries = grouped[idx]
            full_href = self._spine_full_hrefs[idx]
            soup = self._load_soup(full_href)
            if not soup or self._is_skippable_frontbackmatter(soup):
                continue

            parent_entry = None
            child_entries = []
            for entry in file_entries:
                if entry["anchor"] is None:
                    parent_entry = entry
                else:
                    child_entries.append(entry)

            anchor_elements = {}
            for entry in child_entries:
                elem = soup.find(id=entry["anchor"])
                if elem is not None:
                    anchor_elements[entry["anchor"]] = elem

            root_id = None
            if parent_entry is not None:
                root_id = ROOT_ID
                first_heading = soup.find(HEADING_TAGS)
                root_elem = first_heading if first_heading else (soup.body if soup.body else soup)
                anchor_elements[ROOT_ID] = root_elem

            section_texts = self._extract_text(soup, anchor_elements, root_id=root_id)

            for entry in file_entries:
                if entry["anchor"] is not None:
                    sec_id = entry["anchor"]
                else:
                    sec_id = ROOT_ID
                text = section_texts.get(sec_id, "")
                if len(text) < self.min_chars:
                    continue

                toc_path = list(entry["toc_path_tuple"])
                display_path = []
                for i in range(1, len(toc_path) + 1):
                    prefix = tuple(toc_path[:i])
                    display_path.append(path_display_titles.get(prefix, toc_path[i - 1]))

                chapters.append(self._create_chapter(
                    display_path=display_path,
                    toc_path=toc_path,
                    full_text=text,
                    item_id=self._spine_idrefs[idx],
                ))

        for i, ch in enumerate(chapters):
            ch["index"] = i
        self._show_chapters(chapters)
        return chapters

    def _extract_via_headers(self) -> List[Dict[str, Any]]:
        heading_positions = []
        all_text = ""

        skip_spine = self._find_guide_skip_indices()
        for i, full_href in enumerate(self._spine_full_hrefs):
            if i in skip_spine:
                continue
            soup = self._load_soup(full_href)
            if not soup or self._is_skippable_frontbackmatter(soup):
                continue

            self._clean_soup(soup)
            headings_in_this_file = []
            for h_tag in soup.find_all(HEADING_TAGS):
                raw = h_tag.get_text(separator='\n').strip()
                title = self._clean_heading(raw)
                headings_in_this_file.append(title)

            file_text = self._extract_text(soup)
            if not file_text:
                continue

            search_from = 0
            for title in headings_in_this_file:
                escaped = re.escape(title)
                pattern = re.compile(r'\n*' + escaped + r'\s*\n*')
                match = pattern.search(file_text, search_from)
                if match:
                    body_start = match.end()
                    heading_positions.append((title, len(all_text) + body_start))
                    search_from = body_start
                else:
                    heading_positions.append((title, len(all_text)))

            all_text += file_text + " "

        if not heading_positions:
            return []

        chapters = []
        for i, (title, start) in enumerate(heading_positions):
            end = heading_positions[i + 1][1] if i + 1 < len(heading_positions) else len(all_text)
            body = all_text[start:end].strip()
            if len(body) < self.min_chars:
                continue
            chapters.append(self._create_chapter(
                display_path=[title],
                toc_path=[title],
                full_text=body
            ))

        for i, ch in enumerate(chapters):
            ch["index"] = i
        self._show_chapters(chapters)
        return chapters