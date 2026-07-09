import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from bs4 import BeautifulSoup
from epub_utils import Document

# Preserved import for logging helpers (assumed available)
from bbb import utils


class ChapterExtractor:
    """Robust, language‑agnostic chapter extractor for EPUB files using epub-utils."""

    def __init__(self, path: str, force_show: bool = False,
                 preview_words: int = 20, min_chars: int = 200):
        """
        Args:
            doc: epub‑utils Document object.
            force_show: if True, log chapter titles and previews.
            preview_words: number of words in the text preview.
            min_chars: minimum combined text length for a chapter to be kept.
        """
        self.doc = Document(path)
        self.force_show = force_show
        self.min_chars = min_chars
        self.preview_words = preview_words
        self.log = logging.getLogger(__name__)

        # Determine the base directory of the OPF file (e.g., "OEBPS/").
        rootfile_path = self.doc.container.rootfile_path  # e.g., "OEBPS/content.opf"
        self._opf_base = str(Path(rootfile_path).parent) + "/" if Path(rootfile_path).parent != Path('.') else ""

        # Build a list of (full_href, idref) for every spine item in reading order.
        self._spine_full_hrefs: List[str] = []
        self._spine_idrefs: List[str] = []
        self._build_spine_info()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def get_chapter_list(self) -> List[Dict[str, Any]]:
        """Return a list of chapter dictionaries with keys:
        title, toc_title, full_text, word_count, preview, item_id, index.
        """
        chapters = self._extract_from_toc()
        if chapters and len(chapters) >= 2:
            return chapters
        return self._extract_via_headers()

    # ------------------------------------------------------------------
    # Logging / output helpers
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Spine construction
    # ------------------------------------------------------------------
    def _build_spine_info(self) -> None:
        """Fill self._spine_full_hrefs and self._spine_idrefs using the OPF manifest."""
        manifest_items = self.doc.package.manifest.items
        for itemref in self.doc.package.spine.itemrefs:
            idref = itemref['idref']
            # Find the manifest item with this id
            href = None
            for item in manifest_items:
                if item['id'] == idref:
                    href = item['href']
                    break
            if href is None:
                continue
            # Resolve relative href to full path inside the EPUB
            full_href = self._make_full_path(href)
            self._spine_full_hrefs.append(full_href)
            self._spine_idrefs.append(idref)

    def _make_full_path(self, href: str) -> str:
        """Convert a manifest href (relative to OPF) to an absolute ZIP path."""
        if href.startswith('/'):
            return href.lstrip('/')
        return f"{self._opf_base}{href}"

    # ------------------------------------------------------------------
    # TOC target → spine mapping
    # ------------------------------------------------------------------
    def _resolve_toc_target_to_spine_index(self, target: str) -> Optional[int]:
        """
        Given a TOC target (e.g., 'index_split_001.xhtml'), try to find its
        spine index (0‑based) in self._spine_full_hrefs.
        Uses several path variants to handle different publisher conventions.
        """
        # Clean anchor
        base = target.split('#')[0]
        # List of candidate full paths to try
        candidates = [
            base,                                          # exact as given
            self._make_full_path(base),                    # relative to OPF base
            self._make_full_path(Path(base).name),         # filename only
        ]
        # Also try common sub‑folder prefixes
        for prefix in ('xhtml/', 'text/', 'OEBPS/', 'OEBPS/text/', 'OEBPS/xhtml/'):
            candidates.append(self._make_full_path(f"{prefix}{base}"))
            candidates.append(self._make_full_path(f"{prefix}{Path(base).name}"))

        # Remove duplicates while preserving order
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

    # ------------------------------------------------------------------
    # XHTML loading & text extraction
    # ------------------------------------------------------------------
    def _load_soup(self, full_href: str) -> Optional[BeautifulSoup]:
        """Load an XHTML file from the EPUB and return a BeautifulSoup object."""
        try:
            content = self.doc.get_file_by_path(full_href)
            return BeautifulSoup(content.to_str(), "html.parser")
        except (ValueError, AttributeError, KeyError):
            return None

    def _extract_body_text(self, soup: BeautifulSoup) -> str:
        """Extract visible text from a soup, discarding scripts, images, etc."""
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
        """Return the text of the first <h1>‑<h6> in the given XHTML file."""
        soup = self._load_soup(full_href)
        if not soup:
            return None
        h_tag = soup.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
        if not h_tag:
            return None
        raw = h_tag.get_text(separator='\n').strip()
        # Remove common numbering artefacts
        cleaned = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw)
        return cleaned or raw

    # ------------------------------------------------------------------
    # Primary extraction: TOC‑guided spine aggregation
    # ------------------------------------------------------------------
    def _extract_from_toc(self) -> List[Dict[str, Any]]:
        """Build chapter list using the EPUB's table of contents."""
        toc = self.doc.toc
        if not toc:
            return []

        # Flatten all TOC items (parents and children) in depth‑first order.
        all_items = []
        def collect_all(nodes):
            for node in nodes:
                all_items.append(node)
                if node.children:
                    collect_all(node.children)
        collect_all(toc.get_toc_items())
        if not all_items:
            return []

        # Map each TOC item to a spine index.
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

        # Sort by spine index.
        entries.sort(key=lambda e: e["spine_index"])

        # Duplicate indices break the range‑based approach → fall back.
        indices = [e["spine_index"] for e in entries]
        if len(indices) != len(set(indices)):
            self.log.debug("Duplicate spine indices in TOC; falling back to heading extraction.")
            return []

        chapters = []
        for i, entry in enumerate(entries):
            start = entry["spine_index"]
            end = entries[i + 1]["spine_index"] if i + 1 < len(entries) else len(self._spine_full_hrefs)

            # Concatenate text from all spine documents in this chapter's range.
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

            # Title: try the first heading of the first spine document, else TOC label.
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

    # ------------------------------------------------------------------
    # Fallback: split the whole book at HTML headings
    # ------------------------------------------------------------------
    def _extract_via_headers(self) -> List[Dict[str, Any]]:
        """Fallback method: split the concatenated book text at HTML heading elements."""
        heading_positions = []   # (title, cumulative_position)
        all_text = ""

        for full_href in self._spine_full_hrefs:
            soup = self._load_soup(full_href)
            if not soup:
                continue
            for tag in soup(["script", "style", "img", "figure", "svg", "canvas"]):
                tag.decompose()
            # Record each heading and its starting position in the global text.
            for h_tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                raw = h_tag.get_text(separator='\n').strip()
                cleaned = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw)
                title = cleaned or raw
                heading_positions.append((title, len(all_text)))
            # Append the body text of this file.
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