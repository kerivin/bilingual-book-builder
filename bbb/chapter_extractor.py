import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from bs4 import BeautifulSoup
from epub_utils import Document

from bbb import progress, utils


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
        """Return a list of chapter dictionaries with keys:
        title, toc_title, full_text, word_count, preview, item_id, index.
        """
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
        """Fill self._spine_full_hrefs and self._spine_idrefs using the OPF manifest."""
        manifest_items = self.doc.package.manifest.items
        for itemref in self.doc.package.spine.itemrefs:
            idref = itemref['idref']
            href = None
            for item in manifest_items:
                if item['id'] == idref:
                    href = item['href']
                    break
            if href is None:
                continue
            full_href = self._make_full_path(href)
            self._spine_full_hrefs.append(full_href)
            self._spine_idrefs.append(idref)

    def _make_full_path(self, href: str) -> str:
        """Convert a manifest href (relative to OPF) to an absolute ZIP path."""
        if href.startswith('/'):
            return href.lstrip('/')
        return f"{self._opf_base}{href}"

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
        unique_candidates = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)

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

    def _extract_body_text(self, soup: BeautifulSoup) -> str:
        for tag in soup(["script", "style", "img", "figure", "svg", "canvas"]):
            tag.decompose()
        parts = []
        root = soup.body if soup.body else soup
        for elem in root.descendants:
            if elem.name in ("p", "li", "blockquote", "div"):
                text = elem.get_text(" ", strip=True)
                if text:
                    parts.append(text)
        return "\n\n".join(parts)

    def _get_first_heading(self, full_href: str) -> Optional[str]:
        soup = self._load_soup(full_href)
        if not soup:
            return None
        h_tag = soup.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
        if not h_tag:
            return None
        raw = h_tag.get_text(separator='\n').strip()
        cleaned = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw)
        return cleaned or raw

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

        entries = []
        for item in all_items:
            idx = self._resolve_toc_target_to_spine_index(item.target)
            if idx is not None:
                entries.append({
                    "label": item.label,
                    "spine_index": idx,
                    "full_href": self._spine_full_hrefs[idx],
                })
        if not entries:
            return []

        entries.sort(key=lambda e: e["spine_index"])

        indices = [e["spine_index"] for e in entries]
        if len(indices) != len(set(indices)):
            self.log.debug("Duplicate spine indices in TOC; falling back to heading extraction.")
            return []

        chapters = []
        for i, entry in enumerate(entries):
            start = entry["spine_index"]
            end = entries[i + 1]["spine_index"] if i + 1 < len(entries) else len(self._spine_full_hrefs)

            parts = []
            for idx in range(start, end):
                soup = self._load_soup(self._spine_full_hrefs[idx])
                if soup:
                    text = self._extract_body_text(soup)
                    if text:
                        parts.append(text)

            full_text = "\n\n".join(parts).strip()
            if len(full_text) < self.min_chars:
                continue

            heading = self._get_first_heading(self._spine_full_hrefs[start])
            title = heading if heading else entry["label"]

            chapters.append(self._create_chapter(
                title=title,
                toc_title=entry["label"],
                full_text=full_text,
                item_id=self._spine_idrefs[start]
            ))

        for i, ch in enumerate(chapters):
            ch["index"] = i

        self._show_chapters(chapters)
        return chapters

    def _extract_via_headers(self) -> List[Dict[str, Any]]:
        heading_positions = []
        all_text = ""

        for full_href in self._spine_full_hrefs:
            soup = self._load_soup(full_href)
            if not soup:
                continue
            for tag in soup(["script", "style", "img", "figure", "svg", "canvas"]):
                tag.decompose()
            for h_tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                raw = h_tag.get_text(separator='\n').strip()
                cleaned = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw)
                title = cleaned or raw
                heading_positions.append((title, len(all_text)))
            text = soup.get_text(" ", strip=True)
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