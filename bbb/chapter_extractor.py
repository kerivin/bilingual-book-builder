import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from bs4 import BeautifulSoup, NavigableString, Comment
from epub_utils import Document

from bbb import progress, utils

BLOCK_TAGS = {'p', 'div', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
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
                self.log.info(f"{ch['toc_title']}\n{ch['preview']}")
                utils.print_horizontal_line(self.log.info)

    def _create_chapter(self, title: str, toc_title: str, full_text: str,
                        item_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "title": re.sub(r'\n\s*\n+', '\n', title).strip(),
            "toc_title": toc_title,
            "full_text": full_text,
            "word_count": len(full_text.split()),
            "preview": " ".join(full_text.split()[:self.preview_words])
                       + ("…" if len(full_text.split()) > self.preview_words else ""),
            "item_id": item_id
        }

    def _build_spine_info(self) -> None:
        manifest_items = self.doc.package.manifest.items
        for itemref in self.doc.package.spine.itemrefs:
            if itemref.get('linear') == 'no':
                continue
            idref = itemref['idref']
            href = next((item['href'] for item in manifest_items if item['id'] == idref), None)
            if href is None:
                continue
            self._spine_full_hrefs.append(self._make_full_path(href))
            self._spine_idrefs.append(idref)

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
        """Remove unwanted elements in-place."""
        for tag in soup(TAG_BLACKLIST):
            tag.decompose()

        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        for tag in soup.find_all(class_=lambda c: c and set(c.split()) & SKIP_CLASSES):
            tag.decompose()

        for br in soup.find_all("br"):
            br.replace_with("\n")

    def _clean_heading(self, raw: str) -> str:
        """Remove common chapter numbering artefacts from a heading."""
        cleaned = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw)
        return cleaned or raw

    def _extract_body_text(self, soup: BeautifulSoup) -> str:
        self._clean_soup(soup)
        root = soup.body if soup.body else soup

        parts = []

        def walk(node):
            if isinstance(node, NavigableString):
                parts.append(str(node))
                return
            if not hasattr(node, 'name'):
                return
            if node.name == 'br':
                parts.append('\n')
                return
            if node.name in BLOCK_TAGS:
                parts.append('\n\n')
            for child in node.children:
                walk(child)

        walk(root)
        raw_text = ''.join(parts)

        cleaned = re.sub(r'[^\S\n]+', ' ', raw_text)       # spaces/tabs -> space
        cleaned = re.sub(r' *\n *', '\n', cleaned)         # strip space around newlines
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)       # max double newline
        return cleaned.strip()

    def _get_first_heading(self, full_href: str) -> Optional[str]:
        soup = self._load_soup(full_href)
        if not soup:
            return None
        h_tag = soup.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
        if not h_tag:
            return None
        raw = h_tag.get_text(separator='\n').strip()
        return self._clean_heading(raw)

    def _linearize_body(self, node, anchor_elements, current_anchor=None):
        if isinstance(node, NavigableString):
            text = node.strip()
            if text:
                yield (text, current_anchor)
            return
        if not hasattr(node, 'name'):
            return

        if node.name == 'br':
            yield ("\n", current_anchor)
            return

        if node.name in BLOCK_TAGS:
            yield ("\n\n", current_anchor)

        anchor_id = node.get('id') if node.get('id') in anchor_elements else None
        if anchor_id is not None:
            current_anchor = anchor_id
            yield (f"__ANCHOR__{anchor_id}", current_anchor)
            if node.name in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
                return
        for child in node.children:
            yield from self._linearize_body(child, anchor_elements, current_anchor)

    def _extract_from_toc(self) -> List[Dict[str, Any]]:
        toc = self.doc.toc
        if not toc:
            return []

        all_items = []
        def collect_all(nodes):
            for node in nodes:
                all_items.append(node)
                if node.children:
                    collect_all(node.children)
        collect_all(toc.get_toc_items())
        if not all_items:
            return []

        skip_spine = self._find_guide_skip_indices()

        entries = []
        seen_targets = set()
        for item in all_items:
            idx = self._resolve_toc_target_to_spine_index(item.target)
            if idx is None or idx in skip_spine:
                continue
            anchor = item.target.split('#', 1)[1] if '#' in item.target else None
            target_key = (idx, anchor)
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            entries.append({
                "label": item.label,
                "spine_index": idx,
                "anchor": anchor,
                "full_href": self._spine_full_hrefs[idx],
            })
        if not entries:
            return []

        grouped = {}
        order = []
        for e in entries:
            idx = e["spine_index"]
            if idx not in grouped:
                grouped[idx] = []
                order.append(idx)
            grouped[idx].append(e)

        chapters = []
        for idx in order:
            file_entries = grouped[idx]
            full_href = self._spine_full_hrefs[idx]
            soup = self._load_soup(full_href)
            if not soup or self._is_skippable_frontbackmatter(soup):
                continue

            self._clean_soup(soup)

            if len(file_entries) == 1 and file_entries[0]["anchor"] is None:
                text = self._extract_body_text(soup)
                if len(text) >= self.min_chars:
                    heading = self._get_first_heading(full_href)
                    title = heading if heading else file_entries[0]["label"]
                    chapters.append(self._create_chapter(
                        title=title,
                        toc_title=file_entries[0]["label"],
                        full_text=text,
                        item_id=self._spine_idrefs[idx]
                    ))
                continue

            body = soup.body if soup.body else soup

            anchor_elements = {}
            for entry in file_entries:
                if entry["anchor"]:
                    elem = body.find(id=entry["anchor"])
                    if elem is not None:
                        anchor_elements[entry["anchor"]] = elem

            if not anchor_elements:
                entry = file_entries[0]
                text = self._extract_body_text(soup)
                if len(text) >= self.min_chars:
                    heading = self._get_first_heading(full_href)
                    title = heading if heading else entry["label"]
                    chapters.append(self._create_chapter(
                        title=title,
                        toc_title=entry["label"],
                        full_text=text,
                        item_id=self._spine_idrefs[idx]
                    ))
                continue

            items = list(self._linearize_body(body, anchor_elements))
            anchor_texts = {anchor: [] for anchor in anchor_elements}
            current_anchor = None
            for text, anchor_id in items:
                if text.startswith("__ANCHOR__"):
                    current_anchor = text[len("__ANCHOR__"):]
                    continue
                if current_anchor is not None:
                    anchor_texts[current_anchor].append(text)

            for entry in file_entries:
                anchor = entry["anchor"]
                if not anchor or anchor not in anchor_texts:
                    continue
                section_text = "".join(anchor_texts[anchor]).strip()
                if len(section_text) < self.min_chars:
                    continue
                heading = self._get_first_heading(full_href)
                title = entry["label"] if entry["label"] else heading
                chapters.append(self._create_chapter(
                    title=title,
                    toc_title=entry["label"],
                    full_text=section_text,
                    item_id=self._spine_idrefs[idx]
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

            for tag in soup.find_all(list(BLOCK_TAGS)):
                tag.insert_after('\n')

            for h_tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                raw = h_tag.get_text(separator='\n').strip()
                title = self._clean_heading(raw)
                heading_positions.append((title, len(all_text)))

            text = soup.get_text('', strip=False).strip()
            if text:
                all_text += text + " "

        if not heading_positions:
            return []

        chapters = []
        for i, (title, start) in enumerate(heading_positions):
            end = heading_positions[i + 1][1] if i + 1 < len(heading_positions) else len(all_text)
            body = all_text[start:end].strip()
            if len(body) < self.min_chars:
                continue
            chapters.append(self._create_chapter(title, title, body))

        for i, ch in enumerate(chapters):
            ch["index"] = i
        self._show_chapters(chapters)
        return chapters