import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any
import urllib.parse

from bs4 import BeautifulSoup
from ebooklib import epub


class ChapterExtractor:
    def __init__(self, source_epub_path: str, preview_words: int = 20, min_chars: int = 500):
        self.source_epub_path = source_epub_path
        self.min_chars = min_chars
        self.preview_words = preview_words

    # ──────────────────────── primary: TOC ────────────────────────

    def get_chapter_list(self, mode: str = "auto") -> List[Dict[str, Any]]:
        chapters_from_toc = self._extract_via_toc(mode)
        if chapters_from_toc and len(chapters_from_toc) >= 3:
            return chapters_from_toc
        return self._extract_via_headers()

    def _extract_via_toc(self, mode: str) -> List[Dict[str, Any]]:
        try:
            zf, opf_dir, id_to_href, spine_ids, entries = self._parse_toc_and_spine()
        except Exception:
            return []

        if not entries:
            return []

        for entry in entries:
            entry["is_chapter"] = _looks_like_chapter_title(entry["title"])
            entry["is_front_back"] = _is_definitely_not_chapter(entry["title"])

        if mode == "strict":
            chosen = [e for e in entries if e["is_chapter"]]
        elif mode == "loose":
            chosen = [e for e in entries if not e["is_front_back"]]
        else:
            strict_candidates = [e for e in entries if e["is_chapter"]]
            if len(strict_candidates) >= 3:
                chosen = strict_candidates
            else:
                chosen = [e for e in entries if not e["is_front_back"]]

        if not chosen:
            return []

        chapters = []
        seen_ranges = set()
        for entry in chosen:
            start = entry["spine_index"]
            later = [e for e in entries if e["spine_index"] > start]
            end = min(e["spine_index"] for e in later) if later else len(spine_ids)
            spine_range = (start, end)
            if spine_range in seen_ranges:
                continue
            seen_ranges.add(spine_range)

            parts = []
            for idx in range(start, end):
                href = id_to_href[spine_ids[idx]]
                chunk = _extract_text_from_xhtml(zf, opf_dir, href)
                if chunk.strip():
                    parts.append(chunk)
            full_text = "\n\n".join(parts).strip()
            if not full_text or len(full_text) < self.min_chars:
                continue

            chapters.append({
                "title": entry["title"],
                "full_text": full_text,
                "word_count": len(full_text.split()),
                "preview": " ".join(full_text.split()[:self.preview_words])
                + ("…" if len(full_text.split()) > self.preview_words else ""),
            })

        zf.close()
        for i, chapter in enumerate(chapters):
            chapter["index"] = i
        return chapters

    # ────────────────────── fallback: headers ─────────────────────

    def _extract_via_headers(self) -> List[Dict[str, Any]]:
        book = epub.read_epub(self.source_epub_path)

        heading_positions = []
        all_text = ""

        for item_id, _ in book.spine:
            item = book.get_item_with_id(item_id)
            if not item or not isinstance(item, epub.EpubHtml):
                continue

            content = item.get_body_content()
            soup = BeautifulSoup(
                content, "html.parser", from_encoding="utf-8" if content else None
            )
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

        sections = []
        for i in range(len(heading_positions)):
            title, start = heading_positions[i]
            end = (
                heading_positions[i + 1][1]
                if i + 1 < len(heading_positions)
                else len(all_text)
            )
            body = all_text[start:end].strip()
            if len(body) < self.min_chars:
                continue

            sections.append({
                "title": title,
                "full_text": body,
                "word_count": len(body.split()),
                "preview": " ".join(body.split()[:self.preview_words])
                + ("…" if len(body.split()) > self.preview_words else ""),
            })

        for i, section in enumerate(sections):
            section["index"] = i
        return sections

    # ───────────────────── TOC internals ──────────────────────────

    def _parse_toc_and_spine(self):
        zf = zipfile.ZipFile(self.source_epub_path, "r")
        opf_rel = _find_opf_path(zf)
        opf_xml = zf.read(opf_rel)
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        root = ET.fromstring(opf_xml)

        manifest = root.find("opf:manifest", ns)
        spine = root.find("opf:spine", ns)

        id_to_href = {item.attrib["id"]: item.attrib["href"] for item in manifest}
        href_to_id = {v: k for k, v in id_to_href.items()}
        spine_ids = [item.attrib["idref"] for item in spine]
        id_to_spine_idx = {idref: idx for idx, idref in enumerate(spine_ids)}

        opf_dir = "/".join(Path(opf_rel).parent.parts) if "/" in opf_rel else ""
        entries = []

        nav_items = [
            it
            for it in manifest
            if "properties" in it.attrib and "nav" in it.attrib["properties"]
        ]
        if nav_items:
            nav_href = nav_items[0].attrib["href"]
            nav_full = opf_dir + "/" + nav_href if opf_dir else nav_href
            try:
                nav_html = zf.read(nav_full).decode("utf-8", errors="ignore")
            except KeyError:
                pass
            else:
                soup = BeautifulSoup(nav_html, "html.parser")
                toc_nav = soup.find("nav", attrs={"epub:type": "toc"}) or soup.find("nav")
                if toc_nav:
                    for a in toc_nav.find_all("a"):
                        title = a.get_text(strip=True)
                        href = a.get("href", "")
                        if not href:
                            continue
                        href_base = href.split("#")[0]
                        idref = href_to_id.get(href_base)
                        if idref is None:
                            for alt in ("xhtml/" + href_base, href_base.split("/", 1)[-1]):
                                idref = href_to_id.get(alt)
                                if idref:
                                    break
                        if idref is None:
                            continue
                        spine_idx = id_to_spine_idx.get(idref)
                        if spine_idx is None:
                            continue
                        entries.append({"title": title, "spine_index": spine_idx})

        if not entries:
            ncx_items = [
                it
                for it in manifest
                if it.attrib.get("media-type") == "application/x-dtbncx+xml"
            ]
            if ncx_items:
                ncx_href = ncx_items[0].attrib["href"]
                ncx_full = opf_dir + "/" + ncx_href if opf_dir else ncx_href
                try:
                    ncx_xml = zf.read(ncx_full)
                except KeyError:
                    pass
                else:
                    ncx_ns = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}
                    ncx_root = ET.fromstring(ncx_xml)
                    nav_map = ncx_root.find("ncx:navMap", ncx_ns)
                    if nav_map is not None:
                        for nav_point in nav_map.findall(".//ncx:navPoint", ncx_ns):
                            text_el = nav_point.find("ncx:navLabel/ncx:text", ncx_ns)
                            content_el = nav_point.find("ncx:content", ncx_ns)
                            if content_el is None:
                                continue
                            title = text_el.text if text_el is not None else ""
                            src = content_el.attrib.get("src", "")
                            if not src:
                                continue
                            href_base = src.split("#")[0]
                            idref = href_to_id.get(href_base)
                            if idref is None:
                                for alt in ("text/" + href_base, href_base.split("/", 1)[-1]):
                                    idref = href_to_id.get(alt)
                                    if idref:
                                        break
                            if idref is None:
                                continue
                            spine_idx = id_to_spine_idx.get(idref)
                            if spine_idx is None:
                                continue
                            entries.append({"title": title, "spine_index": spine_idx})

        entries.sort(key=lambda e: e["spine_index"])
        return zf, opf_dir, id_to_href, spine_ids, entries


# ─────────────────── shared helpers (module level) ─────────────────

FRONT_BACK_WORDS = {
    "epigraph", "introduction", "preface", "foreword", "acknowledgments",
    "acknowledgements", "acknowledgment", "prologue", "epilogue",
    "about the author", "about the authors", "index", "contents",
    "table of contents", "cover", "title page", "copyright", "dedication",
    "author's note", "author’s note", "further reading", "notes", "endnotes",
    "footnotes", "appendix", "bibliography", "recipe", "recipes",
    "discover more", "also by",
}


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _is_definitely_not_chapter(title: str) -> bool:
    t = _normalize(title)
    if t.startswith("chapter") or t.startswith("capítulo") or t.startswith("capitulo"):
        return False
    for bad in FRONT_BACK_WORDS:
        if t == bad or t.startswith(bad + ":") or t.startswith(bad + " ") or t.startswith("the " + bad):
            return True
    return False

def _looks_like_chapter_title(title: str) -> bool:
    t = _normalize(title)
    # reject bare numbers / Roman numerals
    if re.fullmatch(r"[\d]+\.?", t):
        return False
    if re.fullmatch(r"[ivxlcdm]+", t):
        return False
    # accept if it starts with a number / Roman numeral followed by a word
    if re.match(r"[\d]+[\.\s]+[^\d]+", t):
        return True
    if re.match(r"[ivxlcdm]+[\.\s]+[^\d]+", t):
        return True
    # otherwise, accept anything that isn't front/back matter (loose filter)
    return not _is_definitely_not_chapter(title)


def _sanitize_heading_text(raw: str) -> str:
    """Clean a heading that accidentally contains body text (Ulysses drop‑cap)."""
    cleaned = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw).strip()
    if len(cleaned) > 100:
        cleaned = " ".join(cleaned.split()[:6]) + "…"
    return cleaned if cleaned else raw[:80]


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        return rootfile.attrib["full-path"]
    except Exception:
        for name in zf.namelist():
            if name.lower().endswith(".opf"):
                return name
    raise RuntimeError("OPF not found")


def _extract_text_from_xhtml(zf: zipfile.ZipFile, opf_dir: str, href: str) -> str:
    paths = []
    base = opf_dir + "/" + href if opf_dir else href
    paths.append(base)
    unquoted = urllib.parse.unquote(href)
    if unquoted != href:
        paths.append(opf_dir + "/" + unquoted if opf_dir else unquoted)

    html = None
    for p in paths:
        try:
            html = zf.read(p)
            break
        except KeyError:
            continue
    if html is None:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "img", "figure", "svg"]):
        tag.decompose()

    texts = []
    for elem in soup.body.descendants if soup.body else soup.descendants:
        if elem.name in ("p", "li", "blockquote", "div"):
            txt = elem.get_text(" ", strip=True)
            if txt:
                texts.append(txt)
    return "\n\n".join(texts)