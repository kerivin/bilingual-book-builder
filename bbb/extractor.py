import re
import html
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set

from bs4 import BeautifulSoup, Tag, NavigableString, Comment

from bbb import progress, utils
from bbb.epub_file import EpubFile

HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
TAG_BLACKLIST = {'script', 'style', 'img', 'figure', 'svg', 'canvas'}

SKIP_TOC_LABELS = {
    'titlepage', 'imprint', 'halftitlepage', 'halftitle', 'colophon',
    'copyright', 'also by', 'other books', 'praise for'
}


def _normalize_text(raw):
    text = re.sub(r'[^\S\n]+', ' ', raw)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class Extractor:
    def __init__(self, epub_file: EpubFile, force_show: bool = False,
                 preview_words: int = 20, min_chars: int = 20, fn_prefix: str = 'S_'):
        self.doc = epub_file.document
        self.force_show = force_show
        self.preview_words = preview_words
        self.min_chars = min_chars
        self.fn_prefix = fn_prefix
        self.log = logging.getLogger(__name__)

        rootfile_path = self.doc.container.rootfile_path
        self._opf_base = str(Path(rootfile_path).parent) + "/" if Path(rootfile_path).parent != Path('.') else ""

        self._spine_full_hrefs: List[str] = []
        self._spine_idrefs: List[str] = []
        self._build_spine_info()

        self.footnotes: Dict[str, str] = {}

    def get_chapter_list(self) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        self.footnotes = self._build_global_footnote_map()
        chapters = self._extract_from_toc()
        if chapters and len(chapters) >= 2:
            return chapters, self.footnotes
        chapters = self._extract_via_headers()
        return chapters, self.footnotes

    def _show_chapters(self, chapters: List[Dict[str, Any]]) -> None:
        if not self.force_show:
            return
        with utils.temporary_log_level(self.log, logging.INFO):
            title = self.doc.package.metadata.title
            self.log.info(f"\n{title}\n------")
            for ch in chapters:
                self.log.info(f"{ch['toc_path'][-1] if ch['toc_path'] else ''}\n{ch['preview']}")
                utils.print_horizontal_line(self.log.info)

    def _create_chapter(self, toc_path, heading_html, content_html, item_id,
                        body_class='', footnote_placeholders=None):
        text = BeautifulSoup(content_html, 'html.parser').get_text() if content_html else ''
        word_count = len(text.split())
        preview = ' '.join(text.split()[:self.preview_words])
        if len(text.split()) > self.preview_words:
            preview += '…'
        return {
            'toc_path': toc_path,
            'heading_html': heading_html or '',
            'content_html': content_html or '',
            'word_count': word_count,
            'preview': preview,
            'item_id': item_id,
            'body_class': body_class,
            'footnote_placeholders': footnote_placeholders or []
        }

    def _build_global_footnote_map(self) -> Dict[str, str]:
        footnote_bodies = {}
        candidate_ids: Set[str] = set()
        title_fallbacks: Dict[str, str] = {}

        manifest = self.doc.package.manifest
        xhtml_hrefs = [self._make_full_path(item['href']) for item in manifest.items
                       if item.get('href', '').lower().endswith(('.xhtml', '.html', '.xml'))]

        for full_href in xhtml_hrefs:
            soup = self._load_soup(full_href)
            if not soup:
                continue
            for a_tag in soup.find_all('a', href=True):
                if '#' not in a_tag['href']:
                    continue
                fragment = a_tag['href'].split('#', 1)[1]
                if not fragment:
                    continue
                has_sup = a_tag.find_parent('sup') or a_tag.find('sup')
                text = a_tag.get_text(strip=True)
                if has_sup or re.fullmatch(r'[\d]+|[∗*†‡§¶‖]|\[\d+\]|\(\d+\)', text):
                    candidate_ids.add(fragment)
                    title = a_tag.get('title', '').strip()
                    if title:
                        title_fallbacks[fragment] = title

        for full_href in xhtml_hrefs:
            soup = self._load_soup(full_href)
            if not soup:
                continue
            for fid in list(candidate_ids):
                elem = soup.find(id=fid)
                if not elem:
                    continue
                marker_text = elem.get_text(strip=True)
                if re.fullmatch(r'[\d]+|[∗*†‡§¶‖]|\[\d+\]|\(\d+\)', marker_text):
                    body_parts = []
                    for sibling in elem.find_next_siblings():
                        if sibling.get('id') or sibling.name in HEADING_TAGS:
                            break
                        body_parts.append(str(sibling))
                    footnote_bodies[fid] = ''.join(body_parts)
                else:
                    footnote_bodies[fid] = ''.join(str(c) for c in elem.contents)
                candidate_ids.remove(fid)

        for fid in candidate_ids:
            if fid in title_fallbacks:
                footnote_bodies[fid] = html.escape(title_fallbacks[fid])

        for fid in footnote_bodies:
            body_html = footnote_bodies[fid]
            if not body_html:
                continue
            body_soup = BeautifulSoup(body_html, 'html.parser')
            for a_tag in body_soup.find_all('a', href=True):
                href = a_tag['href'].strip()
                if not href.lower().startswith(('http://', 'https://')):
                    a_tag.decompose()
            footnote_bodies[fid] = str(body_soup)

        return footnote_bodies

    def _build_spine_info(self) -> None:
        manifest_items = {item['id']: item['href'] for item in self.doc.package.manifest.items}
        for itemref in self.doc.package.spine.itemrefs:
            if itemref.get('linear') == 'no':
                continue
            href = manifest_items.get(itemref['idref'])
            if not href:
                continue
            self._spine_full_hrefs.append(self._make_full_path(href))
            self._spine_idrefs.append(itemref['idref'])

    def _make_full_path(self, href: str) -> str:
        if href.startswith('/'):
            return href.lstrip('/')
        return f"{self._opf_base}{href}"

    def _find_guide_skip_indices(self) -> set:
        skip_types = {'cover', 'title-page', 'toc', 'copyright-page'}
        skip_indices = set()
        for ref in getattr(self.doc.package, 'guide', []) or []:
            if ref.get('type', '').lower() in skip_types:
                idx = self._resolve_toc_target_to_spine_index(ref['href'])
                if idx is not None:
                    skip_indices.add(idx)
        return skip_indices

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
        target_basename = Path(base).name
        for i, spine_href in enumerate(self._spine_full_hrefs):
            if Path(spine_href).name == target_basename:
                return i
        return None

    def _load_soup(self, full_href: str) -> Optional[BeautifulSoup]:
        try:
            content = self.doc.get_file_by_path(full_href)
            return BeautifulSoup(content.to_str(), "html.parser")
        except (ValueError, AttributeError, KeyError):
            return None

    def _clean_soup_basic(self, soup: BeautifulSoup) -> None:
        for tag in soup(TAG_BLACKLIST):
            tag.decompose()
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

    def _remove_footnotes_and_placeholders(self, soup: BeautifulSoup) -> List[dict]:
        placeholders = []
        for fid in self.footnotes:
            elem = soup.find(id=fid)
            if elem:
                elem.decompose()

        counter = 0
        for a_tag in soup.find_all('a', href=True):
            if '#' not in a_tag['href']:
                continue
            fragment = a_tag['href'].split('#', 1)[1]
            if fragment not in self.footnotes:
                continue
            has_sup = a_tag.find_parent('sup') or a_tag.find('sup')
            text = a_tag.get_text(strip=True)
            if not (has_sup or re.fullmatch(r'[\d]+|[∗*†‡§¶‖]|\[\d+\]|\(\d+\)', text)):
                continue
            counter += 1
            token = f'{self.fn_prefix}FNREF_{counter}'
            placeholders.append({'token': token, 'target_id': fragment})
            sup_parent = a_tag.find_parent('sup')
            if sup_parent:
                sup_parent.string = token
            else:
                a_tag.replace_with(token)
        return placeholders

    def _extract_content_and_heading(self, soup, start_elem):
        if start_elem is None:
            body = soup.body if soup.body else soup
            content_html = str(body) if body else ''
            heading_html = ''
            if body:
                first_heading = body.find(HEADING_TAGS)
                if first_heading:
                    heading_html = str(first_heading)
            return heading_html, content_html

        collected = [str(start_elem)]
        current = start_elem.find_next_sibling()
        while current is not None:
            if current.name in HEADING_TAGS:
                break
            collected.append(str(current))
            current = current.find_next_sibling()
        content_html = ''.join(collected)
        heading_html = ''
        if start_elem.name in HEADING_TAGS:
            heading_html = str(start_elem)
        else:
            first_heading = start_elem.find(HEADING_TAGS)
            if first_heading:
                heading_html = str(first_heading)
        return heading_html, content_html

    def _extract_chapter_from_toc_entry(self, soup, entry, body_class, placeholders):
        anchor = entry['anchor']
        item_id = self._spine_idrefs[entry['spine_index']]

        if anchor:
            start_elem = soup.find(id=anchor)
        else:
            start_elem = None

        heading_html, content_html = self._extract_content_and_heading(soup, start_elem)

        text = BeautifulSoup(content_html, 'html.parser').get_text() if content_html else ''
        if len(text) < self.min_chars:
            return None

        return self._create_chapter(
            toc_path=list(entry['toc_path']),
            heading_html=heading_html,
            content_html=content_html,
            item_id=item_id,
            body_class=body_class,
            footnote_placeholders=placeholders
        )

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
            label_lower = item.label.strip().lower()
            if label_lower in SKIP_TOC_LABELS:
                continue
            anchor = item.target.split('#', 1)[1] if '#' in item.target else None
            target_key = (idx, anchor)
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            toc_entries.append({
                "label": item.label,
                "toc_path": path,
                "spine_index": idx,
                "anchor": anchor,
                "full_href": self._spine_full_hrefs[idx],
            })
        if not toc_entries:
            return []

        chapters = []
        for entry in toc_entries:
            idx = entry['spine_index']
            full_href = self._spine_full_hrefs[idx]
            soup = self._load_soup(full_href)
            if not soup:
                continue

            self._clean_soup_basic(soup)
            body_class = ' '.join(soup.body.get('class', [])) if soup.body else ''
            anchors_in_file = {e['anchor'] for e in toc_entries if e['spine_index'] == idx and e['anchor']}
            saved_footnotes = self.footnotes
            self.footnotes = {k: v for k, v in self.footnotes.items()
                              if k not in anchors_in_file}
            placeholders = self._remove_footnotes_and_placeholders(soup)
            self.footnotes = saved_footnotes

            ch = self._extract_chapter_from_toc_entry(soup, entry, body_class, placeholders)
            if ch:
                chapters.append(ch)

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
            if not soup:
                continue

            self._clean_soup_basic(soup)
            self._remove_footnotes_and_placeholders(soup)

            headings_in_file = []
            for h_tag in soup.find_all(HEADING_TAGS):
                raw = h_tag.get_text(separator='\n').strip()
                title = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw) or raw
                headings_in_file.append((title, h_tag))

            body = soup.body if soup.body else soup
            file_text = body.get_text(separator='\n')
            file_text = _normalize_text(file_text)
            if not file_text:
                continue

            search_from = 0
            for title, h_tag in headings_in_file:
                escaped = re.escape(title)
                pattern = re.compile(r'\n*' + escaped + r'\s*\n*')
                match = pattern.search(file_text, search_from)
                if match:
                    body_start = match.end()
                    heading_positions.append((title, len(all_text) + body_start, str(h_tag)))
                    search_from = body_start
                else:
                    heading_positions.append((title, len(all_text), str(h_tag)))

            all_text += file_text + " "

        if not heading_positions:
            return []

        chapters = []
        for i, (title, start, heading_html) in enumerate(heading_positions):
            end = heading_positions[i+1][1] if i+1 < len(heading_positions) else len(all_text)
            body = all_text[start:end].strip()
            if len(body) < self.min_chars:
                continue
            wrapped_html = f'<div>{body}</div>'
            chapters.append(self._create_chapter(
                toc_path=[title],
                heading_html=heading_html,
                content_html=wrapped_html,
                item_id=None,
                body_class='',
                footnote_placeholders=[]
            ))

        for i, ch in enumerate(chapters):
            ch["index"] = i
        self._show_chapters(chapters)
        return chapters