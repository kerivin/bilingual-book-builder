import os
import uuid
import re
from typing import List, Dict, Any, Optional
from ebooklib import epub
import ebooklib
import logging

from bs4 import BeautifulSoup, Tag

from bbb import progress
from bbb.epub_file import EpubFile
from bbb.constants import SRC_FN_PREFIX, TGT_FN_PREFIX


class BookBuilder:
    def __init__(self, source_book: EpubFile, target_book: EpubFile,
                 blocks: List[Dict[str, Any]],
                 copy_target_cover=False,
                 source_footnotes=None, target_footnotes=None):
        self.source_book = source_book.ebook
        self.target_book = target_book.ebook
        self.blocks = blocks
        self.copy_target_cover = copy_target_cover
        self.source_footnotes = source_footnotes or {}
        self.target_footnotes = target_footnotes or {}
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

    def _copy_styles(self, new_book: epub.EpubBook) -> List[str]:
        return []

    def _copy_metadata(self, new_book: epub.EpubBook):
        def get_metadata(book, key):
            return [val[0] for val in book.get_metadata('DC', key)] if book else []

        src_titles = get_metadata(self.source_book, 'title')
        tgt_titles = get_metadata(self.target_book, 'title')

        title = ''
        if src_titles and tgt_titles:
            title = f"{src_titles[0]} / {tgt_titles[0]}"
        elif tgt_titles:
            title = tgt_titles[0]
        elif src_titles:
            title = src_titles[0]
        new_book.set_title(title)

        def set_authors_from(book):
            if book is None:
                return
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

        set_authors_from(self.source_book)
        set_authors_from(self.target_book)

        tgt_langs = get_metadata(self.target_book, 'language')
        if tgt_langs:
            new_book.set_language(tgt_langs[0])
        for lang in get_metadata(self.source_book, 'language'):
            if lang not in get_metadata(self.target_book, 'language'):
                new_book.add_metadata('DC', 'language', lang)

        for key in ('publisher', 'date', 'description', 'subject', 'contributor', 'rights'):
            src_vals = get_metadata(self.source_book, key)
            tgt_vals = get_metadata(self.target_book, key)
            for val in src_vals + tgt_vals:
                new_book.add_metadata('DC', key, val)

        identifier = None
        tgt_ids = get_metadata(self.target_book, 'identifier')
        if tgt_ids:
            identifier = tgt_ids[0]
        if not identifier:
            src_ids = get_metadata(self.source_book, 'identifier')
            if src_ids:
                identifier = src_ids[0]
        if not identifier:
            identifier = f"urn:uuid:{uuid.uuid4()}"
        new_book.set_identifier(identifier)

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

        def replacer(m):
            token = m.group(1)
            target_id = token_map.get(token)
            if not target_id:
                return token
            num = used_numbers[0] + 1
            used_numbers[0] = num
            fn_body = global_footnotes.get(target_id, '')
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

    def _build_footnote_list(self, footnote_items: list) -> str:
        if not footnote_items:
            return ''
        items = ''.join(
            f'<li id="{fn["id"]}">{fn["body"]} '
            f'<a href="#{fn["ref_id"]}" class="footnote-backref">↩</a></li>'
            for fn in footnote_items
        )
        return f'<hr class="footnote-separator"/><div class="footnotes"><ol>{items}</ol></div>'

    def _set_text_indent(self, html: str, indent: str) -> str:
        soup = BeautifulSoup(f'<root>{html}</root>', 'html.parser')
        root = soup.root

        target = None
        for elem in root.descendants:
            if elem is None or not isinstance(elem, Tag):
                continue
            if elem.name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                break
            if elem.name == 'p' or 'paragraph' in elem.get('class', []):
                target = elem
                break

        if target is None:
            for elem in root.descendants:
                if elem is None or not isinstance(elem, Tag):
                    continue
                if elem.name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                    break
                if elem.name in ('div', 'p', 'blockquote', 'section'):
                    target = elem
                    break

        if target is not None:
            target['style'] = f"text-indent: {indent} !important;"

        return ''.join(str(c) for c in root.contents)

    def _build_two_column_html(self, aligned_paras):
        rows = []
        for para in aligned_paras:
            for i, seg in enumerate(para):
                src_html = seg.get('source_html', '')
                tgt_html = seg.get('target_html', '')

                if i == 0:
                    src_html = self._set_text_indent(src_html, '2em')
                    tgt_html = self._set_text_indent(tgt_html, '2em')
                else:
                    src_html = self._set_text_indent(src_html, '0')
                    tgt_html = self._set_text_indent(tgt_html, '0')

                rows.append(
                    f'<tr>'
                    f'<td class="bilingual-left">{src_html}</td>'
                    f'<td class="bilingual-right">{tgt_html}</td>'
                    f'</tr>'
                )
        return '<table class="bilingual-table">' + ''.join(rows) + '</table>'

    def run(self) -> epub.EpubBook | None:
        new_book = epub.EpubBook()
        self._copy_metadata(new_book)
        self._copy_cover(new_book)
        css_links = self._copy_styles(new_book)

        base_dir = self._get_base_dir()
        generic_css = epub.EpubItem(
            uid="generic_css",
            file_name=base_dir + "generic.css",
            media_type='text/css',
            content=b"""
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
            }
            h1 { font-size: 1.6em; }
            h2 { font-size: 1.4em; }
            h3 { font-size: 1.2em; }
            h4 { font-size: 1.1em; }
            h5 { font-size: 1em; }
            h6 { font-size: 0.9em; }

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

            .bilingual-left *, .bilingual-right * {
                margin: 0 !important;
                padding: 0 !important;
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
        )
        new_book.add_item(generic_css)
        css_links.append(generic_css.file_name)

        new_spine_ids = []
        toc_entries = []

        with progress.phase('building', len(self.blocks), "Building chapters"):
            for block_idx, block in enumerate(self.blocks):
                source = block.get('source')
                target = block.get('target')
                if not source and not target:
                    continue

                alignment = block.get('alignment', [])
                if source and target and alignment:
                    body_content = self._build_two_column_html(alignment)
                elif source:
                    body_content = source.get('content_html', '')
                elif target:
                    body_content = target.get('content_html', '')
                else:
                    body_content = ''

                token_map = {}
                if source and source.get('footnote_placeholders'):
                    for p in source['footnote_placeholders']:
                        token_map[p['token']] = p['target_id']
                if target and target.get('footnote_placeholders'):
                    for p in target['footnote_placeholders']:
                        token_map[p['token']] = p['target_id']

                global_fns = {}
                global_fns.update(self.source_footnotes)
                global_fns.update(self.target_footnotes)

                used_numbers = [0]
                body_content, footnote_items = self._replace_footnote_tokens(
                    body_content, token_map, global_fns, used_numbers
                )
                footnotes_html = self._build_footnote_list(footnote_items)

                full_body = body_content + '\n' + footnotes_html

                toc_path = []
                if target:
                    toc_path = target['toc_path']
                elif source:
                    toc_path = source['toc_path']
                flat_title = toc_path[-1] if toc_path else ''

                file_name = f"{base_dir}chap_{block_idx:03d}.xhtml"
                uid = f"chap_{block_idx:03d}"
                item = epub.EpubHtml(
                    uid=uid,
                    file_name=file_name,
                    media_type='application/xhtml+xml'
                )
                item.title = flat_title
                item.set_content(full_body)
                for css_path in css_links:
                    item.add_link(href=css_path, rel='stylesheet', type='text/css')
                new_book.add_item(item)

                new_spine_ids.append(item.get_id())
                toc_entries.append({
                    'file_name': item.file_name,
                    'uid': item.get_id(),
                    'toc_path': toc_path,
                })

                progress.update('building')

        new_book.add_item(epub.EpubNcx())
        new_book.add_item(epub.EpubNav())
        new_book.spine = new_spine_ids
        new_book.toc = self._build_toc(toc_entries)
        return new_book