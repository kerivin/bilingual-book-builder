import re
import html
import posixpath
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set, OrderedDict

from bs4 import BeautifulSoup, Comment, NavigableString, ProcessingInstruction, Tag

from bbb import utils
from bbb.constants import HEADING_TAGS, BLOCK_TAGS
from bbb.epub_file import EpubFile

HEADINGISH_TAGS = {'hgroup', *HEADING_TAGS}
TAG_BLACKLIST = {'script', 'style', 'img', 'figure', 'svg', 'canvas'}

SKIP_CHAPTER_TYPES = {
    'titlepage', 'title-page', 'imprint', 'halftitlepage', 'halftitle', 'colophon',
    'copyright', 'copyright-page', 'also by', 'other books', 'praise for',
    'cover', 'toc',
    'frontmatter', 'backmatter', 'acknowledgements',
    'other.frontmatter', 'other.backmatter'
}

KEEP_CHAPTER_TYPES = {
    'dedication', 'foreword', 'preface', 'introduction', 'prologue',
    'epigraph', 'acknowledgments', 'afterword', 'conclusion',
    'part', 'chapter'
}

FN_MARKER_RE = re.compile(r'[\d]+|[∗*†‡§¶‖]|\[\d+\]|\(\d+\)')
PLAIN_FN_REF_RE = re.compile(r'\[(\d+)\]')
PLAIN_FN_BODY_TAGS = BLOCK_TAGS - HEADING_TAGS


class FootnoteExtractor:
    def __init__(self, host):
        self.host = host
        self.footnotes: Dict[str, str] = {}
        self._footnote_files: Dict[str, str] = {}
        self.current_refs: List[Dict[str, str]] = []
        self._plain_seq = 0

    def build_map(self) -> Dict[str, str]:
        footnote_bodies = {}
        candidate_ids: Set[str] = set()
        candidate_targets: Dict[str, str] = {}
        title_fallbacks: Dict[str, str] = {}

        manifest = self.host.doc.package.manifest
        xhtml_hrefs = [self.host._make_full_path(item['href']) for item in manifest.items
                       if item.get('href', '').lower().endswith(('.xhtml', '.html', '.xml'))]

        for full_href in xhtml_hrefs:
            soup = self.host._load_soup(full_href)
            if not soup:
                continue
            for a_tag in soup.find_all('a', href=True):
                is_fn, fragment = self._is_footnote_reference(a_tag)
                if not is_fn:
                    continue
                candidate_ids.add(fragment)
                href_base = a_tag['href'].split('#', 1)[0]
                candidate_targets[fragment] = self.host._resolve_relative_href(full_href, href_base)
                self._footnote_files[fragment] = full_href
                title = a_tag.get('title', '').strip()
                if title:
                    title_fallbacks[fragment] = title

        preferred_hrefs = set(candidate_targets.values())
        scan_order = [h for h in xhtml_hrefs if h in preferred_hrefs]
        scan_order += [h for h in xhtml_hrefs if h not in preferred_hrefs]

        for full_href in scan_order:
            soup = self.host._load_soup(full_href)
            if not soup:
                continue
            for fid in list(candidate_ids):
                if candidate_targets.get(fid) != full_href:
                    continue
                elem = soup.find(id=fid)
                if not elem:
                    continue
                marker_text = elem.get_text(strip=True)
                if FN_MARKER_RE.fullmatch(marker_text):
                    body_parts = []
                    for sibling in elem.find_next_siblings():
                        if sibling.get('id') or sibling.name in HEADING_TAGS:
                            break
                        body_parts.append(str(sibling))
                    footnote_bodies[fid] = ''.join(body_parts)
                else:
                    contents = list(elem.contents)
                    if not contents and elem.parent is not None:
                        first_child = next((c for c in elem.parent.contents
                                            if not (isinstance(c, NavigableString)
                                                    and not str(c).strip())), None)
                        if first_child is elem:
                            contents = [c for c in elem.parent.contents if c is not elem]
                            if not contents:
                                contents = list(elem.parent.contents)
                    footnote_bodies[fid] = ''.join(
                        str(c) for c in self._strip_leading_marker(contents)
                    )
                self._footnote_files[fid] = full_href
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

        self.footnotes = footnote_bodies
        return self.footnotes

    def _footnote_container(self, elem) -> Tag:
        marker_text = elem.get_text(strip=True)
        if FN_MARKER_RE.fullmatch(marker_text):
            for sibling in elem.find_next_siblings():
                if sibling.get('id') or sibling.name in HEADING_TAGS:
                    break
                sibling.decompose()
            return elem
        if elem.contents:
            return elem
        parent = elem.parent
        if parent is not None:
            first_child = next((c for c in parent.contents
                                if not (isinstance(c, NavigableString)
                                        and not str(c).strip())), None)
            if first_child is elem:
                return parent
        return elem

    def _prune_empty_ancestors(self, node) -> None:
        while node is not None and node.name != 'body':
            if any(c for c in node.contents
                   if not (isinstance(c, NavigableString) and not str(c).strip())):
                break
            parent = node.parent
            node.decompose()
            node = parent

    def remove_from(self, soup: BeautifulSoup, full_href: Optional[str] = None) -> None:
        self.current_refs = []

        for fid in self.footnotes:
            if self._footnote_files.get(fid) != full_href:
                continue
            elem = soup.find(id=fid)
            if not elem:
                continue
            container = self._footnote_container(elem) if self.footnotes[fid].strip() else elem
            ancestor = container.parent
            container.decompose()
            self._prune_empty_ancestors(ancestor)

        counter = 0
        for a_tag in soup.find_all('a', href=True):
            is_fn, fragment = self._is_footnote_reference(a_tag)
            if not is_fn:
                continue
            if fragment not in self.footnotes:
                continue
            if not self.footnotes[fragment].strip():
                continue

            counter += 1
            token = f'{self.host.fn_prefix}FNREF_{counter}'
            self.current_refs.append({
                'token': token,
                'target_id': fragment
            })
            a_tag.replace_with(token)

        if counter == 0:
            self._remove_plain_footnotes(soup, full_href)

    @staticmethod
    def _inside_tag(node, names) -> bool:
        parent = node.parent
        while parent is not None:
            if getattr(parent, 'name', None) in names:
                return True
            parent = parent.parent
        return False

    def _plain_sections(self, soup) -> List[List[Tag]]:
        body = soup.body if soup.body else soup
        sections: List[List[Tag]] = []
        current: List[Tag] = []
        for el in body.find_all(True):
            name = getattr(el, 'name', None)
            if not name:
                continue
            if name in TAG_BLACKLIST:
                continue
            if name in HEADING_TAGS:
                if current:
                    sections.append(current)
                    current = []
                continue
            if name not in PLAIN_FN_BODY_TAGS:
                continue
            if any(a.name in PLAIN_FN_BODY_TAGS for a in el.parents):
                continue
            current.append(el)
        if current:
            sections.append(current)
        return sections

    def _plain_markers(self, blocks) -> List[Dict[str, Any]]:
        markers = []
        for idx, el in enumerate(blocks):
            text = _normalize_text(el.get_text(' '))
            m = re.match(r'^\[(\d+)\]\s*(.+)$', text, re.S)
            if m and len(m.group(2).strip()) >= 3:
                markers.append({'num': int(m.group(1)), 'elem': el, 'idx': idx})
        return markers

    def _plain_refs(self, soup, blocks, exclude_ids) -> List[Dict[str, Any]]:
        block_ids = {id(el): idx for idx, el in enumerate(blocks)}
        refs = []
        body = soup.body if soup.body else soup
        for node in body.find_all(string=True):
            if not isinstance(node, NavigableString):
                continue
            if self._inside_tag(node, ('a', 'sup', 'script', 'style')):
                continue
            blk = node.parent
            while blk is not None and blk.name not in PLAIN_FN_BODY_TAGS:
                blk = blk.parent
            if blk is None or id(blk) not in block_ids or id(blk) in exclude_ids:
                continue
            for m in PLAIN_FN_REF_RE.finditer(str(node)):
                refs.append({
                    'num': int(m.group(1)),
                    'node': node,
                    'match': m,
                    'block_idx': block_ids[id(blk)]
                })
        refs.sort(key=lambda r: r['block_idx'])
        return refs

    def _validate_plain(self, markers, refs) -> Optional[List[Dict[str, Any]]]:
        if not markers or not refs:
            return None
        last_ref = max(r['block_idx'] for r in refs)
        group = [m for m in markers if m['idx'] > last_ref]
        if not group:
            return None
        nums = [m['num'] for m in group]
        if nums != list(range(1, len(nums) + 1)):
            return None
        ref_nums = set(r['num'] for r in refs)
        if not all(n in ref_nums for n in nums):
            return None
        return group

    @staticmethod
    def _strip_plain_marker_html(el) -> str:
        parts = list(el.contents)
        while parts:
            node = parts[0]
            if isinstance(node, NavigableString):
                text = str(node).lstrip()
                if not text:
                    parts.pop(0)
                    continue
                stripped = re.sub(r'^\[(\d+)\]\s*', '', text, count=1)
                if stripped != text:
                    parts[0] = NavigableString(stripped)
                break
            if isinstance(node, Tag) and not node.get_text(strip=True):
                parts.pop(0)
                continue
            break
        return ''.join(str(c) for c in parts)

    def _remove_plain_footnotes(self, soup, full_href) -> None:
        counter = 0
        for blocks in self._plain_sections(soup):
            markers = self._plain_markers(blocks)
            if not markers:
                continue
            refs = self._plain_refs(soup, blocks, exclude_ids={id(m['elem']) for m in markers})
            group = self._validate_plain(markers, refs)
            if group is None:
                continue
            marker_elems = {m['elem'] for m in markers}
            group_idxs = [m['idx'] for m in group]
            num_to_fid = {}
            for i, m in enumerate(group):
                self._plain_seq += 1
                fid = f'{self.host.fn_prefix}PTFN_{self._plain_seq}'
                num_to_fid[m['num']] = fid
                body_html = self._strip_plain_marker_html(m['elem'])
                if i + 1 < len(group_idxs):
                    for b in blocks[m['idx'] + 1:group_idxs[i + 1]]:
                        if b in marker_elems:
                            continue
                        if not _normalize_text(b.get_text(' ')):
                            continue
                        body_html += str(b)
                        b.decompose()
                ancestor = m['elem'].parent
                m['elem'].decompose()
                self._prune_empty_ancestors(ancestor)
                self.footnotes[fid] = body_html
                self._footnote_files[fid] = full_href

            by_node = {}
            for r in refs:
                if r['block_idx'] >= group_idxs[0]:
                    continue
                if r['num'] not in num_to_fid:
                    continue
                counter += 1
                token = f'{self.host.fn_prefix}FNREF_{counter}'
                self.current_refs.append({
                    'token': token,
                    'target_id': num_to_fid[r['num']]
                })
                by_node.setdefault(r['node'], []).append(
                    (r['match'].start(), r['match'].end(), token)
                )
            for node, replacements in by_node.items():
                parts = []
                last = 0
                for start, end, token in sorted(replacements):
                    parts.append(str(node)[last:start])
                    parts.append(token)
                    last = end
                parts.append(str(node)[last:])
                node.replace_with(''.join(parts))

    def _is_note_epubtype(self, a_tag) -> bool:
        for node in (a_tag, *a_tag.parents):
            etype = node.get('epub:type', '')
            if isinstance(etype, str) and any(
                t in etype.split() for t in ('noteref', 'footnote')
            ):
                return True
        return False

    def _is_footnote_reference(self, a_tag):
        href = a_tag.get('href', '')
        if '#' not in href:
            return False, None
        fragment = href.split('#', 1)[1]
        if not fragment:
            return False, None
        if self._is_note_epubtype(a_tag):
            return True, fragment
        if a_tag.find_parent('sup') or a_tag.find('sup'):
            return True, fragment
        text = a_tag.get_text(strip=True)
        if not FN_MARKER_RE.fullmatch(text) or text.isdigit():
            return False, None
        return True, fragment

    @staticmethod
    def _strip_leading_marker(contents) -> list:
        parts = list(contents)
        while parts:
            node = parts[0]
            if isinstance(node, NavigableString):
                text = node.strip()
                if not text:
                    parts.pop(0)
                    continue
                if FN_MARKER_RE.fullmatch(text):
                    parts.pop(0)
                    continue
                break
            if hasattr(node, 'name') and FN_MARKER_RE.fullmatch(node.get_text(strip=True)):
                parts.pop(0)
                continue
            break
        return parts or list(contents)


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
        self._build_spine_info()

        self.footnotes: Dict[str, str] = {}
        self.footnotes_extractor = FootnoteExtractor(self)

    def get_chapter_list(self) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        self.footnotes = self.footnotes_extractor.build_map()
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
                toc_label = ch['toc_path'][-1] if ch['toc_path'] else ""
                self.log.info(f"{toc_label}\n{ch['preview']}")
                utils.print_horizontal_line(self.log.info)

    def _create_chapter(self, toc_path: List[str], content_html: str,
                        footnote_placeholders: Optional[List[Dict]] = None) -> Dict[str, Any]:
        text = BeautifulSoup(content_html, 'html.parser').get_text(separator=' ') if content_html else ''
        preview = ' '.join(text.split()[:self.preview_words])
        if len(text.split()) > self.preview_words:
            preview += '…'
        return {
            'toc_path': toc_path,
            'content_html': content_html or '',
            'preview': preview,
            'footnote_placeholders': footnote_placeholders or []
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

    def _make_full_path(self, href: str) -> str:
        if href.startswith('/'):
            return href.lstrip('/')
        return f"{self._opf_base}{href}"

    def _resolve_relative_href(self, from_full_href: str, href: str) -> str:
        if not href:
            return from_full_href
        if href.startswith('/'):
            return href.lstrip('/')
        return posixpath.normpath(posixpath.join(posixpath.dirname(from_full_href), href))

    def _find_guide_skip_indices(self) -> set:
        skip_indices = set()
        for ref in getattr(self.doc.package, 'guide', []) or []:
            if ref.get('type', '').lower() in SKIP_CHAPTER_TYPES:
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

    def _is_skippable_frontbackmatter(self, soup: BeautifulSoup) -> bool:
        if not soup.body:
            return False

        for tag in soup.body.descendants:
            if not hasattr(tag, 'get'):
                continue
            etype = tag.get('epub:type', '')
            if not isinstance(etype, str):
                continue
            if any(t in etype.split() for t in KEEP_CHAPTER_TYPES):
                return False

        body_etype = soup.body.get('epub:type', '')
        if isinstance(body_etype, str) and ('frontmatter' in body_etype or 'backmatter' in body_etype):
            return True

        for child in soup.body.find_all(True, recursive=False):
            etype = child.get('epub:type', '')
            if isinstance(etype, str) and ('frontmatter' in etype or 'backmatter' in etype):
                return True
        return False

    def _load_soup(self, full_href: str) -> Optional[BeautifulSoup]:
        try:
            content = self.doc.get_file_by_path(full_href)
            if content is None:
                return None
            return BeautifulSoup(content.to_str(), "html.parser")
        except (ValueError, AttributeError, KeyError, TypeError):
            return None

    def _clean_soup(self, soup: BeautifulSoup, handle_footnotes: bool = True,
                    full_href: Optional[str] = None) -> None:
        for tag in soup(TAG_BLACKLIST):
            tag.decompose()
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()
        for pi in soup.find_all(string=lambda text: isinstance(text, ProcessingInstruction)):
            pi.extract()
        if handle_footnotes:
            self.footnotes_extractor.remove_from(soup, full_href)

    def _extract_html_regions(self, soup: BeautifulSoup,
                              anchor_elements: Dict[str, Any],
                              root_id: Optional[str] = None) -> Dict[str, str]:
        root = soup.body if soup.body else soup

        if not anchor_elements:
            anchor_elements = {'__default__': root}
            if root_id is None:
                root_id = '__default__'

        region_html = OrderedDict((aid, []) for aid in anchor_elements)

        def walk(node, anchor):
            if isinstance(node, NavigableString):
                if anchor is not None:
                    region_html[anchor].append(str(node))
                return anchor
            if not hasattr(node, 'name'):
                return anchor

            node_anchor = anchor
            if node.get('id') in anchor_elements:
                node_anchor = node.get('id')
            elif root_id and node is anchor_elements.get(root_id):
                node_anchor = root_id

            if node_anchor is not None:
                region_html[node_anchor].append(str(node))
            else:
                for child in node.children:
                    anchor = walk(child, anchor)
            return node_anchor

        walk(root, root_id if root_id in anchor_elements else None)
        return {aid: ''.join(pieces) for aid, pieces in region_html.items()}

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
            label = item.label or ''
            label_lower = label.strip().lower()
            if label_lower in SKIP_CHAPTER_TYPES:
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

        grouped: Dict[int, List[dict]] = OrderedDict()
        for entry in toc_entries:
            idx = entry["spine_index"]
            if idx not in grouped:
                grouped[idx] = []
            grouped[idx].append(entry)

        chapters = []
        ROOT_ID = "__root__"

        for idx in grouped:
            file_entries = grouped[idx]
            full_href = self._spine_full_hrefs[idx]
            soup = self._load_soup(full_href)
            if not soup or self._is_skippable_frontbackmatter(soup):
                continue

            self._clean_soup(soup, handle_footnotes=True, full_href=full_href)

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
                    if elem.parent and elem.parent.name in HEADINGISH_TAGS:
                        elem = elem.parent
                    anchor_elements[entry["anchor"]] = elem

            root_id = None
            if parent_entry is not None:
                root_id = ROOT_ID
                first_heading = soup.find(HEADING_TAGS)
                root_elem = first_heading if first_heading else (soup.body if soup.body else soup)
                if first_heading and first_heading.parent and first_heading.parent.name in HEADINGISH_TAGS:
                    root_elem = first_heading.parent
                anchor_elements[ROOT_ID] = root_elem

            region_html = self._extract_html_regions(soup, anchor_elements, root_id=root_id)
            footnote_refs = self.footnotes_extractor.current_refs

            for entry in file_entries:
                sec_id = entry["anchor"] if entry["anchor"] is not None else ROOT_ID
                html_content = region_html.get(sec_id, "")
                text = BeautifulSoup(html_content, 'html.parser').get_text() if html_content else ''
                if len(text) < self.min_chars:
                    continue

                chapters.append(self._create_chapter(
                    toc_path=list(entry["toc_path"]),
                    content_html=html_content,
                    footnote_placeholders=footnote_refs
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

            self._clean_soup(soup, handle_footnotes=False)
            headings_in_file = []
            for h_tag in soup.find_all(HEADING_TAGS):
                raw = h_tag.get_text(separator='\n').strip()
                title = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw) or raw
                headings_in_file.append(title)

            body = soup.body if soup.body else soup
            file_text = body.get_text(separator='\n')
            file_text = _normalize_text(file_text)
            if not file_text:
                continue

            search_from = 0
            for title in headings_in_file:
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
            wrapped_html = f'<div>{body}</div>'
            chapters.append(self._create_chapter(
                toc_path=[title],
                content_html=wrapped_html
            ))

        for i, ch in enumerate(chapters):
            ch["index"] = i
        self._show_chapters(chapters)
        return chapters