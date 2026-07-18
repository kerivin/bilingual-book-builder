import re
import html
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from collections import OrderedDict, defaultdict

from bs4 import BeautifulSoup, NavigableString, Comment

from bbb import progress, utils
from bbb.epub_file import EpubFile
from bbb.html_tokenizer import tokenize, Token

HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
HEADINGISH_TAGS = {'hgroup', *HEADING_TAGS}
BLOCK_TAGS = {'p', 'div', 'li', 'blockquote', *HEADING_TAGS}
TAG_BLACKLIST = {'script', 'style', 'img', 'figure', 'svg', 'canvas'}
BR_PLACEHOLDER = '\uE000'
TEXT_BR_PLACEHOLDER = '__BR__'
DEFAULT_ANCHOR_KEY = '__default__'


def _normalize_text(raw):
    text = re.sub(r'[^\S\n]+', ' ', raw)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class Extractor:
    def __init__(self, epub_file: EpubFile, force_show: bool = False,
                 preview_words: int = 20, min_chars: int = 100, fn_prefix: str = 'S_'):
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

    def get_chapter_list(self) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        self.log.info("Extracting chapters via ToC...")
        self.footnotes = self._build_global_footnote_map()
        toc_chapters = self._extract_from_toc()
        if toc_chapters and len(toc_chapters) >= 2:
            return toc_chapters, self.footnotes
        self.log.info("Extracting chapters via headers...")
        h_chapters = self._extract_via_headers()
        if h_chapters and len(h_chapters) >= 2:
            return h_chapters, self.footnotes
        elif toc_chapters:
            return toc_chapters, self.footnotes
        else:
            return h_chapters, self.footnotes

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

    def _create_chapter(self, toc_path: List[str],
                        full_text: str, item_id: Optional[str] = None,
                        footnote_refs: Optional[List] = None,
                        paragraph_tokens: Optional[List] = None,
                        body_class: str = '') -> Dict[str, Any]:
        ch = {
            "toc_path": toc_path,
            "full_text": full_text,
            "word_count": len(full_text.split()),
            "preview": " ".join(full_text.split()[:self.preview_words])
                       + ("…" if len(full_text.split()) > self.preview_words else ""),
            "item_id": item_id
        }
        if footnote_refs is not None:
            ch["footnote_refs"] = footnote_refs
        if paragraph_tokens is not None:
            ch["paragraph_tokens"] = paragraph_tokens
        if body_class:
            ch["body_class"] = body_class
        return ch

    def _build_global_footnote_map(self) -> Dict[str, str]:
        footnote_bodies = {}
        candidate_ids = set()
        title_fallbacks = {}

        manifest = self.doc.package.manifest
        xhtml_hrefs = [self._make_full_path(item['href']) for item in manifest.items
                       if item.get('href', '').lower().endswith(('.xhtml', '.html', '.xml'))]

        for full_href in xhtml_hrefs:
            soup = self._load_soup(full_href)
            if not soup:
                continue
            for a_tag in soup.find_all('a', href=True):
                is_fn, fragment = self._is_footnote_reference(a_tag)
                if is_fn:
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
                if elem is None:
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

    def _clean_soup(self, soup: BeautifulSoup, handle_footnotes: bool = True) -> None:
        for tag in soup(TAG_BLACKLIST):
            tag.decompose()
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()
        if handle_footnotes:
            self._remove_footnotes(soup)

    def _is_footnote_reference(self, a_tag):
        href = a_tag.get('href', '')
        if '#' not in href:
            return False, None
        fragment = href.split('#', 1)[1]
        if not fragment:
            return False, None
        has_sup = a_tag.find_parent('sup') is not None or a_tag.find('sup') is not None
        text = a_tag.get_text(strip=True)
        is_marker = bool(re.fullmatch(r'[\d]+|[∗*†‡§¶‖]|\[\d+\]|\(\d+\)', text))
        if has_sup or is_marker:
            return True, fragment
        return False, None

    def _remove_footnotes(self, soup: BeautifulSoup) -> None:
        self._current_footnote_refs = []
        for fid in self.footnotes:
            elem = soup.find(id=fid)
            if elem:
                elem.decompose()
        counter = 0
        for a_tag in soup.find_all('a', href=True):
            is_fn, fragment = self._is_footnote_reference(a_tag)
            if not is_fn:
                continue
            if fragment not in self.footnotes:
                continue
            counter += 1
            token = f'{self.fn_prefix}FNREF_{counter}'
            self._current_footnote_refs.append({
                'token': token,
                'target_id': fragment
            })
            a_tag.replace_with(token)

    def _get_heading_text(self, elem) -> str:
        if elem is None:
            return ""
        for br in elem.find_all('br'):
            br.replace_with('\n')
        return elem.get_text().strip()

    def _extract_heading_with_newlines(self, elem) -> str:
        if elem is None:
            return ""
        if elem.name in HEADINGISH_TAGS:
            return self._get_heading_text(elem)
        for tag in HEADINGISH_TAGS:
            found = elem.find(tag)
            if found:
                return self._get_heading_text(found)
        return ""

    def _extract_paragraphs_and_tokens(self, soup: BeautifulSoup,
                                       anchor_elements: Dict[str, Any],
                                       root_id: Optional[str] = None):
        body = soup.body if soup.body else soup
        if root_id is not None and root_id not in anchor_elements:
            root_id = None

        anchor_paragraphs = defaultdict(list)
        current_anchor = root_id

        def _split_block_at_br(block_elem):
            """
            Tokenize a block element and split its token list at each <br/>.
            Each segment is wrapped with the block's opening/closing tags.
            Returns a list of token lists (paragraphs).
            """
            tokens = tokenize(block_elem)
            if not tokens:
                return []

            # The first token is the open tag, the last is the close tag.
            open_tokens = []
            close_tokens = []
            if tokens[0].kind == 'open':
                open_tokens.append(tokens[0])
                close_tokens.append(tokens[-1])
                inner = tokens[1:-1]
            else:
                # Not a typical block; just treat as one paragraph.
                return [tokens]

            # Split inner token list at <br/> (void tokens with 'br' tag)
            segments = []
            current_segment = []
            for tok in inner:
                if tok.kind == 'void' and '<br' in tok.content:
                    # flush current segment
                    if current_segment:
                        segments.append(open_tokens + current_segment + close_tokens)
                        current_segment = []
                    # The <br/> itself can be included as a separate paragraph?
                    # Usually we don't want an empty paragraph for a lone <br/>.
                    # We'll just skip it (like original behavior).
                else:
                    current_segment.append(tok)
            if current_segment:
                segments.append(open_tokens + current_segment + close_tokens)
            return segments if segments else [tokens]   # if no <br/>, return original

        def process_block(block_elem):
            if current_anchor is None:
                return
            for tokens in _split_block_at_br(block_elem):
                if tokens:
                    anchor_paragraphs[current_anchor].append(tokens)

        def walk_children(node):
            nonlocal current_anchor
            if isinstance(node, NavigableString):
                text = str(node)
                if text.strip() and current_anchor is not None:
                    anchor_paragraphs[current_anchor].append([Token('text', text, len(text))])
                return
            if not hasattr(node, 'name'):
                return

            if hasattr(node, 'get') and node.get('id') in anchor_elements:
                old = current_anchor
                current_anchor = node.get('id')
                for child in node.children:
                    walk_children(child)
                current_anchor = old
                return

            if node.name in BLOCK_TAGS:
                process_block(node)
            else:
                for child in node.children:
                    walk_children(child)

        walk_children(body)

        result = {}
        for aid, para_list in anchor_paragraphs.items():
            plain_paras = []
            for tokens in para_list:
                para_text = ''.join(t.content for t in tokens if t.kind == 'text')
                plain_paras.append(para_text)
            full_text = '\n\n'.join(plain_paras)
            full_text = _normalize_text(full_text)
            result[aid] = {'text': full_text, 'paragraph_tokens': para_list}
        return result

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
                "toc_path": path,
                "spine_index": idx,
                "anchor": anchor,
                "full_href": self._spine_full_hrefs[idx],
            })
        if not toc_entries:
            return []

        path_display_titles = {}
        for entry in toc_entries:
            soup = self._load_soup(entry["full_href"])
            if not soup:
                continue
            for tag in soup(TAG_BLACKLIST):
                tag.decompose()
            heading_text = ""
            if entry["anchor"] is not None:
                elem = soup.find(id=entry["anchor"])
                heading_text = self._extract_heading_with_newlines(elem) if elem else ""
            else:
                root = soup.body if soup.body else soup
                heading_text = self._extract_heading_with_newlines(root)
            path_display_titles[entry["toc_path"]] = heading_text if heading_text else entry["label"]

        grouped = defaultdict(list)
        for e in toc_entries:
            grouped[e["spine_index"]].append(e)
        order = sorted(grouped.keys())

        chapters = []
        ROOT_ID = DEFAULT_ANCHOR_KEY

        for spine_idx in order:
            file_entries = grouped[spine_idx]
            full_href = self._spine_full_hrefs[spine_idx]
            soup = self._load_soup(full_href)
            if not soup or self._is_skippable_frontbackmatter(soup):
                continue

            body_class = ' '.join(soup.body.get('class', [])) if soup.body else ''

            anchor_ids_in_file = set()
            for entry in file_entries:
                if entry["anchor"] is not None:
                    anchor_ids_in_file.add(entry["anchor"])

            saved_footnotes = self.footnotes
            self.footnotes = {k: v for k, v in self.footnotes.items() if k not in anchor_ids_in_file}
            self._clean_soup(soup, handle_footnotes=True)
            current_footnote_refs = self._current_footnote_refs
            self.footnotes = saved_footnotes

            anchor_elements = {}
            for entry in file_entries:
                if entry["anchor"] is not None:
                    elem = soup.find(id=entry["anchor"])
                    if elem is not None:
                        anchor_elements[entry["anchor"]] = elem

            root_id = None
            if any(e["anchor"] is None for e in file_entries):
                root_id = ROOT_ID
                first_heading = soup.find(HEADING_TAGS)
                root_elem = first_heading if first_heading else (soup.body if soup.body else soup)
                anchor_elements[ROOT_ID] = root_elem

            section_data = self._extract_paragraphs_and_tokens(soup, anchor_elements, root_id=root_id)

            for entry in file_entries:
                sec_id = entry["anchor"] if entry["anchor"] is not None else ROOT_ID
                data = section_data.get(sec_id)
                if data is None:
                    continue
                text = data['text']
                if len(text) < self.min_chars:
                    continue
                toc_path = list(entry["toc_path"])
                chapters.append(self._create_chapter(
                    toc_path=toc_path,
                    full_text=text,
                    item_id=self._spine_idrefs[spine_idx],
                    footnote_refs=current_footnote_refs,
                    paragraph_tokens=data['paragraph_tokens'],
                    body_class=body_class
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
            headings_in_this_file = []
            for h_tag in soup.find_all(HEADING_TAGS):
                raw = h_tag.get_text(separator='\n').strip()
                title = re.sub(r"^\[?\d+\]?\s*[-–—]?\s*\[?\d+\]?\s*", "", raw) or raw
                headings_in_this_file.append(title)

            file_text = self._extract_text(soup, handle_footnotes=False)
            if not file_text:
                continue
            file_text = file_text.get(DEFAULT_ANCHOR_KEY, "")
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
                toc_path=[title],
                full_text=body
            ))

        for i, ch in enumerate(chapters):
            ch["index"] = i
        self._show_chapters(chapters)
        return chapters

    def _extract_text(self, soup: BeautifulSoup, anchor_elements=None, root_id=None, handle_footnotes=True):
        self._clean_soup(soup, handle_footnotes)
        for br in soup.find_all('br'):
            br.replace_with(TEXT_BR_PLACEHOLDER)
        root = soup.body if soup.body else soup
        if not anchor_elements:
            anchor_elements = {DEFAULT_ANCHOR_KEY: root}
            if root_id is None:
                root_id = DEFAULT_ANCHOR_KEY
        anchor_texts = OrderedDict((aid, []) for aid in anchor_elements)
        current_anchor = root_id if root_id in anchor_elements else None

        def walk(node):
            nonlocal current_anchor
            if isinstance(node, NavigableString):
                if current_anchor is not None:
                    anchor_texts[current_anchor].append(str(node))
                return
            if not hasattr(node, 'name'):
                return
            anchor_id = node.get('id') if node.get('id') in anchor_elements else None
            if anchor_id is not None:
                current_anchor = anchor_id
                if node.name in HEADINGISH_TAGS:
                    return
            elif root_id and node is anchor_elements.get(root_id):
                current_anchor = root_id
                if node.name in HEADINGISH_TAGS:
                    return
            if node.name in BLOCK_TAGS and current_anchor is not None:
                anchor_texts[current_anchor].append('\n\n')
            for child in node.children:
                walk(child)
        walk(root)
        result = {}
        for aid, pieces in anchor_texts.items():
            raw = ''.join(pieces)
            raw = raw.replace(TEXT_BR_PLACEHOLDER, '\n')
            result[aid] = _normalize_text(raw)
        return result