import re
from typing import List, Dict, Any
from bs4 import BeautifulSoup
from fast_ebook import epub, ITEM_DOCUMENT
from enum import Enum

class FilterMode(Enum):
    HEADING = 0
    NOT_NUMBER = 1

def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _sanitize_heading_text(raw: str) -> str:
    cleaned = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw).strip()
    if len(cleaned) > 100:
        cleaned = " ".join(cleaned.split()[:6]) + "…"
    return cleaned if cleaned else raw[:80]

class ChapterExtractor:
    def __init__(self, book: epub.EpubBook, preview_words: int = 20, min_chars: int = 500):
        self.book = book
        self.min_chars = min_chars
        self.preview_words = preview_words

    def get_chapter_list(self) -> List[Dict[str, Any]]:
        chapters = self._extract_native_toc(FilterMode.HEADING)
        if chapters and len(chapters) >= 2:
            return chapters
        chapters = self._extract_native_toc(FilterMode.NOT_NUMBER)
        if chapters and len(chapters) >= 2:
            return chapters
        return self._extract_via_headers()

    def _create_chapter(self, title, full_text):
        return {
            "title": title,
            "full_text": full_text,
            "word_count": len(full_text.split()),
            "preview": " ".join(full_text.split()[:self.preview_words])
            + ("…" if len(full_text.split()) > self.preview_words else ""),
        }

    def _has_heading(self, doc) -> bool:
        content = doc.get_content()
        if not content:
            return False
        soup = BeautifulSoup(content.decode('utf-8', errors='replace'), "html.parser")
        return soup.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']) is not None

    def _extract_native_toc(self, filter_mode: FilterMode) -> List[Dict[str, Any]]:
        if not self.book.toc:
            return []

        flat_entries = []
        def flatten(items):
            for item in items:
                flat_entries.append((item.title, item.href))
                if item.children:
                    flatten(item.children)
        flatten(self.book.toc)

        spine_hrefs = []
        spine_idrefs = []
        for idref, _ in self.book.get_spine():
            item = self.book.get_item_with_id(idref)
            if item:
                spine_hrefs.append(item.get_name())
                spine_idrefs.append(idref)

        entries = []
        for title, href in flat_entries:
            if filter_mode == FilterMode.NOT_NUMBER:
                if re.fullmatch(r"[\d]+\.?", _normalize(title)):
                    continue

            doc = self.book.get_item_with_href(href)
            if doc is None:
                for variant in (f"xhtml/{href}", f"text/{href}", href.split('/')[-1]):
                    doc = self.book.get_item_with_href(variant)
                    if doc:
                        break
            if doc is None:
                continue

            if filter_mode == FilterMode.HEADING and not self._has_heading(doc):
                continue

            idx = None
            for variant in (href, f"xhtml/{href}", f"text/{href}", href.split('/')[-1]):
                try:
                    idx = spine_hrefs.index(variant)
                    break
                except ValueError:
                    continue
            if idx is None:
                continue
            entries.append({"title": title, "spine_index": idx})

        if not entries:
            return []

        entries.sort(key=lambda e: e["spine_index"])

        chapters = []
        for i, entry in enumerate(entries):
            start = entry["spine_index"]
            end = entries[i + 1]["spine_index"] if i + 1 < len(entries) else len(spine_hrefs)

            parts = []
            for idx in range(start, end):
                idref = spine_idrefs[idx]
                item = self.book.get_item_with_id(idref)
                if item:
                    content = item.get_content()
                    if content:
                        soup = BeautifulSoup(content.decode('utf-8', errors='replace'), "html.parser")
                        for tag in soup(["script", "style", "img", "figure", "svg", "canvas"]):
                            tag.decompose()
                        for h_tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                            h_tag.decompose()
                        text = soup.get_text(" ", strip=True)
                        if text:
                            parts.append(text)

            full_text = "\n\n".join(parts).strip()
            if len(full_text) < self.min_chars:
                continue

            chapters.append(self._create_chapter(entry["title"], full_text))

        for i, chapter in enumerate(chapters):
            chapter["index"] = i
        return chapters

    def _extract_via_headers(self) -> List[Dict[str, Any]]:
        heading_positions = []
        all_text = ""

        for idref, _ in self.book.get_spine():
            item = self.book.get_item_with_id(idref)
            if item is None:
                continue
            content = item.get_content()
            if not content:
                continue
            soup = BeautifulSoup(content.decode('utf-8', errors='replace'), "html.parser")
            for tag in soup(["script", "style", "img", "figure", "svg", "canvas"]):
                tag.decompose()

            for h_tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                raw_title = " ".join(h_tag.stripped_strings)
                clean_title = _sanitize_heading_text(raw_title)
                heading_positions.append((clean_title, len(all_text)))

            text = soup.get_text(" ", strip=True)
            if text:
                all_text += text + " "

        if not heading_positions:
            return []

        chapters = []
        for i in range(len(heading_positions)):
            title, start = heading_positions[i]
            end = heading_positions[i + 1][1] if i + 1 < len(heading_positions) else len(all_text)
            body = all_text[start:end].strip()
            if len(body) < self.min_chars:
                continue

            chapters.append(self._create_chapter(title, body))

        for i, section in enumerate(chapters):
            section["index"] = i
        return chapters