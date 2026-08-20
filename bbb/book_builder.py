import os
import uuid
import re
import posixpath
from typing import List, Dict, Any
from ebooklib import epub
import logging

from bs4 import BeautifulSoup, Tag

from bbb.epub_file import EpubFile
from bbb.progress import ProgressReporter
from bbb.constants import BLOCK_TAGS, SRC_FN_PREFIX, TGT_FN_PREFIX

GENERIC_CSS = b"""
            body {
                margin: 0;
                padding: 0;
            }

            b, strong { font-weight: bold; }
            i, em, cite { font-style: italic; }
            u { text-decoration: underline; }
            small { font-size: 0.8em; }
            sub, sup { font-size: 0.75em; line-height: 0; position: relative; vertical-align: baseline; }
            sup { top: -0.5em; }
            sub { bottom: -0.25em; }

            h1, h2, h3, h4, h5, h6 {
                font-weight: bold;
                margin: 0.5em 0 0.2em 0;
                padding: 0;
                text-align: center;
            }
            h1 { font-size: 1.6em; }
            h2 { font-size: 1.4em; }
            h3 { font-size: 1.2em; }
            h4 { font-size: 1.1em; }
            h5 { font-size: 1em; }
            h6 { font-size: 0.9em; }

            .subtitle {
                font-weight: bold;
                text-align: center;
                margin: 0.5em 0;
            }

            p, .paragraph {
                display: block;
            }
            blockquote {
                display: block;
                margin: 0.5em 1.5em;
            }

            .bilingual-table {
                width: 100% !important;
                border-collapse: collapse;
                table-layout: fixed !important;
                font-size: inherit !important;
            }
            .bilingual-table td {
                display: table-cell !important;
                vertical-align: top;
                padding: 0.3em 1em;
                width: 50% !important;
                box-sizing: border-box !important;
            }
            .bilingual-table,
            .bilingual-table tr,
            .bilingual-table td {
                border: 0 none transparent !important;
            }

            .bilingual-left, .bilingual-right {
            }

            .bilingual-left *, .bilingual-right * {
                margin: 0 !important;
                padding: 0 !important;
                text-indent: 0 !important;
                float: none !important;
                clear: none !important;
                position: static !important;
                width: auto !important;
                max-width: none !important;
                min-width: 0 !important;
                height: auto !important;
                max-height: none !important;
                min-height: 0 !important;
                box-sizing: content-box !important;
            }

            .bilingual-left .bl-p-start,
            .bilingual-right .bl-p-start {
                text-indent: 2em !important;
            }
            .bl-block {
                display: block !important;
            }

            .footnote-ref {
                font-size: 0.75em;
                vertical-align: super;
                line-height: 0;
            }
            .footnote-separator {
                width: 30%;
                margin: 2em auto 1em;
            }
            .footnotes {
                font-size: 0.9em;
            }
            .footnote-backref {
                font-size: 0.8em;
                text-decoration: none;
            }
            """


class BookBuilder:
    def __init__(self, source_book: EpubFile, target_book: EpubFile,
                 blocks: List[Dict[str, Any]],
                 copy_target_cover=False,
                 source_footnotes=None, target_footnotes=None,
                 progress_reporter=None):
        self.source_book = source_book.ebook
        self.target_book = target_book.ebook
        self.blocks = blocks
        self.copy_target_cover = copy_target_cover
        self.source_footnotes = source_footnotes or {}
        self.target_footnotes = target_footnotes or {}
        self.progress = progress_reporter or ProgressReporter()
        self.log = logging.getLogger(__name__)

    def _get_base_dir(self) -> str:
        spine = self.target_book.spine
        if not spine:
            return "text/"
        first_id = spine[0][0]
        item = self.target_book.get_item_with_id(first_id)
        if item is None:
            return "text/"
        href = item.file_name
        return os.path.dirname(href) + "/" if os.path.dirname(href) else ""

    def _relative_href(self, file_name: str) -> str:
        base_dir = self._get_base_dir()
        if not base_dir:
            return file_name
        return posixpath.relpath(file_name, base_dir)

    def _copy_cover(self, new_book: epub.EpubBook) -> None:
        cover_id = None
        book = self.target_book if self.copy_target_cover else self.source_book
        opf_meta = book.get_metadata(epub.NAMESPACES['OPF'], 'meta')
        for val, attrs in opf_meta:
            if attrs.get('name') == 'cover':
                cover_id = attrs.get('content')
                break
        cover_item = book.get_item_with_id(cover_id) if cover_id else None
        if cover_item:
            new_book.set_cover(cover_item.file_name, cover_item.get_content())

    @staticmethod
    def _get_metadata(book, key):
        return [val[0] for val in book.get_metadata('DC', key)] if book else []

    def _copy_title(self, new_book: epub.EpubBook):
        src_titles = self._get_metadata(self.source_book, 'title')
        tgt_titles = self._get_metadata(self.target_book, 'title')

        title = ''
        if src_titles and tgt_titles:
            title = f"{src_titles[0]} / {tgt_titles[0]}"
        elif tgt_titles:
            title = tgt_titles[0]
        elif src_titles:
            title = src_titles[0]
        new_book.set_title(title)

    def _copy_authors(self, new_book: epub.EpubBook):
        for book in (self.source_book, self.target_book):
            for creator in book.get_metadata('DC', 'creator'):
                attrs = creator[1] if len(creator) > 1 else {}
                known = {}
                if 'opf:role' in attrs:
                    known['role'] = attrs['opf:role']
                if 'opf:file-as' in attrs:
                    known['file_as'] = attrs['opf:file-as']
                try:
                    new_book.add_author(creator[0], **known)
                except TypeError:
                    new_book.add_author(creator[0])

    def _copy_language(self, new_book: epub.EpubBook):
        tgt_langs = self._get_metadata(self.target_book, 'language')
        if tgt_langs:
            new_book.set_language(tgt_langs[0])
        for lang in self._get_metadata(self.source_book, 'language'):
            if lang not in self._get_metadata(self.target_book, 'language'):
                new_book.add_metadata('DC', 'language', lang)

    def _copy_other_metadata(self, new_book: epub.EpubBook):
        for key in ('publisher', 'date', 'description', 'subject', 'contributor', 'rights'):
            src_vals = self._get_metadata(self.source_book, key)
            tgt_vals = self._get_metadata(self.target_book, key)
            for val in src_vals + tgt_vals:
                new_book.add_metadata('DC', key, val)

    def _copy_identifier(self, new_book: epub.EpubBook):
        identifier = None
        tgt_ids = self._get_metadata(self.target_book, 'identifier')
        if tgt_ids:
            identifier = tgt_ids[0]
        if not identifier:
            src_ids = self._get_metadata(self.source_book, 'identifier')
            if src_ids:
                identifier = src_ids[0]
        if not identifier:
            identifier = f"urn:uuid:{uuid.uuid4()}"
        new_book.set_identifier(identifier)

    def _copy_metadata(self, new_book: epub.EpubBook):
        self._copy_title(new_book)
        self._copy_authors(new_book)
        self._copy_language(new_book)
        self._copy_other_metadata(new_book)
        self._copy_identifier(new_book)

    def _build_toc(self, flat_entries):
        tree = {}
        for entry in flat_entries:
            path = entry['toc_path']
            link = epub.Link(entry['file_name'], path[-1] if path else '', entry['uid'])
            node = tree
            for i, part in enumerate(path):
                if part not in node:
                    node[part] = {'link': None, 'sub': {}}
                if i == len(path) - 1:
                    node[part]['link'] = link
                node = node[part]['sub']

        def build(subtree):
            items = []
            for label, info in subtree.items():
                child = build(info['sub'])
                link = info['link']
                if link is not None:
                    if child:
                        items.append((epub.Section(label, href=link.href), child))
                    else:
                        items.append(link)
                else:
                    if child:
                        items.append((epub.Section(label), child))
            return items

        return build(tree)

    def _replace_footnote_tokens(self, text: str, token_map: Dict[str, str],
                                 global_footnotes: Dict[str, str],
                                 used_numbers: List[int]) -> tuple[str, list]:
        token_pattern = re.compile(rf'({re.escape(SRC_FN_PREFIX)}FNREF_\d+|{re.escape(TGT_FN_PREFIX)}FNREF_\d+)')
        footnote_items = []
        target_numbers = {}

        def replacer(m):
            token = m.group(1)
            target_id = token_map.get(token)
            if not target_id:
                return token
            if target_id in target_numbers:
                num = target_numbers[target_id]
                return f'<sup class="footnote-ref" id="fnref_{num}"><a href="#fn_{num}">[{num}]</a></sup>'
            num = used_numbers[0] + 1
            used_numbers[0] = num
            target_numbers[target_id] = num
            fn_body = global_footnotes.get(target_id, '')
            if not fn_body.strip():
                return f'<sup class="footnote-ref">{num}</sup>'
            ref_id = f'fnref_{num}'
            fn_id = f'fn_{num}'
            footnote_items.append({
                'id': fn_id,
                'ref_id': ref_id,
                'body': fn_body
            })
            return f'<sup class="footnote-ref" id="{ref_id}"><a href="#{fn_id}">[{num}]</a></sup>'

        processed = token_pattern.sub(replacer, text)
        return processed, footnote_items

    def _append_backref(self, body: str, ref_id: str) -> str:
        backref = BeautifulSoup(
            f'<a href="#{ref_id}" class="footnote-backref">↩</a>',
            'html.parser'
        ).a
        soup = BeautifulSoup(body, 'html.parser')
        leaf = None
        for tag in soup.find_all(True):
            if tag.get_text(strip=True):
                leaf = tag
        target = soup
        if leaf is not None:
            target = leaf
            while target.name not in BLOCK_TAGS and target.parent is not None:
                target = target.parent
        target.append(' ')
        target.append(backref)
        return str(soup)

    def _build_footnote_list(self, footnote_items: list) -> str:
        if not footnote_items:
            return ''
        items = ''.join(
            f'<li id="{fn["id"]}">{self._append_backref(fn["body"], fn["ref_id"])}</li>'
            for fn in footnote_items
        )
        return f'<hr class="footnote-separator"/><div class="footnotes"><ol>{items}</ol></div>'

    def _insert_footnotes(self, body_content: str, source, target) -> str:
        token_map = {}
        for side in (source, target):
            if side and side.get('footnote_placeholders'):
                for p in side['footnote_placeholders']:
                    token_map[p['token']] = p['target_id']

        global_fns = {}
        global_fns.update(self.source_footnotes)
        global_fns.update(self.target_footnotes)

        used_numbers = [0]
        body_content, footnote_items = self._replace_footnote_tokens(
            body_content, token_map, global_fns, used_numbers
        )
        footnotes_html = self._build_footnote_list(footnote_items)
        if not footnotes_html:
            return body_content

        close_body = body_content.rfind('</body>')
        if close_body != -1:
            return body_content[:close_body] + footnotes_html + body_content[close_body:]
        return body_content + '\n' + footnotes_html

    def _apply_indent_to_block(self, html_str: str) -> str:
        soup = BeautifulSoup(html_str, 'html.parser')
        first_tag = soup.contents[0] if isinstance(soup.contents[0], Tag) else soup.find()
        if first_tag is not None and first_tag.name in BLOCK_TAGS:
            classes = first_tag.get('class', [])
            if 'bl-p-start' not in classes:
                first_tag['class'] = [*classes, 'bl-p-start']
            return str(soup)
        return f'<span class="bl-p-start bl-block">{html_str}</span>'

    def _build_two_column_html(self, aligned_rows):
        rows = []
        for row in aligned_rows:
            src_sents = row.get('source_sents', [])
            tgt_sents = row.get('target_sents', [])

            src_parts = []
            for s in src_sents:
                html = s['html']
                if s.get('first'):
                    html = self._apply_indent_to_block(html)
                src_parts.append(html)
            src_html = '\n'.join(src_parts)

            tgt_parts = []
            for t in tgt_sents:
                html = t['html']
                if t.get('first'):
                    html = self._apply_indent_to_block(html)
                tgt_parts.append(html)
            tgt_html = '\n'.join(tgt_parts)

            rows.append(
                f'<tr>'
                f'<td class="bilingual-left">{src_html}</td>'
                f'<td class="bilingual-right">{tgt_html}</td>'
                f'</tr>'
            )
        return '<table class="bilingual-table">' + ''.join(rows) + '</table>'

    def _add_generic_css(self, new_book: epub.EpubBook, base_dir: str) -> str:
        css = epub.EpubItem(
            uid="generic_css",
            file_name=base_dir + "generic.css",
            media_type='text/css',
            content=GENERIC_CSS,
        )
        new_book.add_item(css)
        return css.file_name

    def _build_chapter(self, block_idx: int, block: Dict[str, Any], base_dir: str, css_path: str):
        source = block.get('source')
        target = block.get('target')
        if not source and not target:
            return None, None

        alignment = block.get('alignment', [])
        if source and target and alignment:
            body_content = self._build_two_column_html(alignment)
        elif source:
            body_content = source.get('content_html', '')
        elif target:
            body_content = target.get('content_html', '')
        else:
            body_content = ''

        body_content = self._insert_footnotes(body_content, source, target)

        toc_path = []
        if target:
            toc_path = target['toc_path']
        elif source:
            toc_path = source['toc_path']

        file_name = f"{base_dir}chap_{block_idx:03d}.xhtml"
        uid = f"chap_{block_idx:03d}"
        item = epub.EpubHtml(
            uid=uid,
            file_name=file_name,
            media_type='application/xhtml+xml'
        )
        item.title = toc_path[-1] if toc_path else ''
        item.set_content(body_content)
        if css_path:
            item.add_link(href=self._relative_href(css_path), rel='stylesheet', type='text/css')
        toc_entry = {
            'file_name': item.file_name,
            'uid': item.get_id(),
            'toc_path': toc_path,
        }
        return item, toc_entry

    def run(self) -> epub.EpubBook | None:
        new_book = epub.EpubBook()
        self._copy_metadata(new_book)
        self._copy_cover(new_book)

        base_dir = self._get_base_dir()
        css_path = self._add_generic_css(new_book, base_dir)

        new_spine_ids = []
        toc_entries = []

        with self.progress.phase('building', len(self.blocks), "Building chapters"):
            for block_idx, block in enumerate(self.blocks):
                item, toc_entry = self._build_chapter(block_idx, block, base_dir, css_path)
                if item is None:
                    continue
                new_book.add_item(item)
                new_spine_ids.append(item.get_id())
                toc_entries.append(toc_entry)
                self.progress.update('building')

        new_book.add_item(epub.EpubNcx())
        new_book.add_item(epub.EpubNav())
        new_book.spine = new_spine_ids
        new_book.toc = self._build_toc(toc_entries)
        return new_book