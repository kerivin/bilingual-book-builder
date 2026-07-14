import os
import uuid
import html
from typing import List, Dict, Any, Optional
from ebooklib import epub
import ebooklib
import logging
from bbb import progress


class BookBuilder:
    def __init__(self, source_path: str, target_path: str, blocks: List[Dict[str, Any]],
                 copy_target_cover=False,
                 source_footnotes=None, target_footnotes=None):
        self.source_path = source_path
        self.target_path = target_path
        self.blocks = blocks
        self.copy_target_cover = copy_target_cover
        self.source_footnotes = source_footnotes or {}
        self.target_footnotes = target_footnotes or {}
        self.log = logging.getLogger(__name__)

    @staticmethod
    def _escape(text: str) -> str:
        return html.escape(text, quote=False)

    @staticmethod
    def _inline_html(text: str) -> str:
        return BookBuilder._escape(text).replace('\n', '<br/>\n')

    @staticmethod
    def _paragraphs_html(text: str) -> str:
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        return '\n'.join(
            f'<p>{BookBuilder._inline_html(p)}</p>' for p in paragraphs
        )

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

        cover_item = None
        if cover_id:
            cover_item = book.get_item_with_id(cover_id)

        if cover_item:
            new_book.set_cover(cover_item.file_name, cover_item.get_content())

    def _copy_styles(self, new_book: epub.EpubBook) -> List[str]:
        css_links = []
        for item in self.target_book.get_items():
            if item.get_type() == ebooklib.ITEM_STYLE:
                css = epub.EpubItem(
                    uid=item.get_id(),
                    file_name=item.file_name,
                    media_type='text/css',
                    content=item.get_content()
                )
                new_book.add_item(css)
                css_links.append(item.file_name)
        return css_links

    def _copy_metadata(self, new_book: epub.EpubBook):
        def get_metadata(book, key):
            return [val[0] for val in book.get_metadata('DC', key)] if book else []

        src_titles = get_metadata(self.source_book, 'title') if self.source_book else []
        tgt_titles = get_metadata(self.target_book, 'title')

        title: str = ""
        if src_titles and tgt_titles:
            title = f"{src_titles[0]} / {tgt_titles[0]}"
        elif tgt_titles:
            title = tgt_titles[0]
        elif src_titles:
            title = src_titles[0]

        self.log.info(f"New book title: {title}")
        new_book.set_title(title)

        def set_authors_from(book):
            authors: str = ""
            for creator in book.get_metadata('DC', 'creator'):
                attrs = creator[1] if len(creator) > 1 else {}
                known_kwargs = {}
                if 'opf:role' in attrs:
                    known_kwargs['role'] = attrs['opf:role']
                if 'opf:file-as' in attrs:
                    known_kwargs['file_as'] = attrs['opf:file-as']
                try:
                    new_book.add_author(creator[0], **known_kwargs)
                    authors += f"{creator[0]}, "
                except TypeError:
                    new_book.add_author(creator[0])
                    authors += f"{creator[0]}, "
            self.log.info(f"New book authors: {authors}")

        set_authors_from(self.source_book)
        set_authors_from(self.target_book)

        tgt_langs = get_metadata(self.target_book, 'language')
        if tgt_langs:
            new_book.set_language(tgt_langs[0])
        for lang in get_metadata(self.source_book, 'language'):
            if lang not in get_metadata(self.target_book, 'language'):
                new_book.add_metadata('DC', 'language', lang)

        src_pubs = get_metadata(self.source_book, 'publisher')
        tgt_pubs = get_metadata(self.target_book, 'publisher')
        for pub in src_pubs + tgt_pubs:
            new_book.add_metadata('DC', 'publisher', pub)

        tgt_dates = get_metadata(self.target_book, 'date')
        if tgt_dates:
            for date in tgt_dates:
                new_book.add_metadata('DC', 'date', date)
        for date in get_metadata(self.source_book, 'date'):
            new_book.add_metadata('DC', 'date', date)

        for key in ('description', 'subject', 'contributor', 'rights'):
            src_vals = get_metadata(self.source_book, key)
            tgt_vals = get_metadata(self.target_book, key)
            for val in src_vals + tgt_vals:
                new_book.add_metadata('DC', key, val)

        identifier = None
        target_identifiers = get_metadata(self.target_book, 'identifier')
        if target_identifiers:
            identifier = target_identifiers[0]
        if not identifier:
            source_identifiers = get_metadata(self.source_book, 'identifier')
            if source_identifiers:
                identifier = source_identifiers[0]
        if not identifier:
            identifier = f"urn:uuid:{uuid.uuid4()}"
        new_book.set_identifier(identifier)

    def _build_toc(self, flat_entries: List[Dict[str, Any]]) -> list:
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

        def build(items_subtree):
            toc_items = []
            for label, info in items_subtree.items():
                child_toc = build(info['sub'])
                link = info['link']
                if link is not None:
                    if child_toc:
                        section = epub.Section(label, href=link.href)
                        toc_items.append((section, child_toc))
                    else:
                        toc_items.append(link)
                else:
                    if child_toc:
                        section = epub.Section(label)
                        toc_items.append((section, child_toc))
            return toc_items

        return build(tree)

    def _create_xhtml_item(self, book: epub.EpubBook, file_name: str, title: str,
                           body_html: str, css_links: List[str], uid: Optional[str] = None):
        if uid is None:
            uid = f"chap_{uuid.uuid4().hex[:8]}"
        item = epub.EpubHtml(
            uid=uid,
            file_name=file_name,
            media_type='application/xhtml+xml'
        )
        item.title = title
        item.set_content(body_html)
        for css_path in css_links:
            item.add_link(href=css_path, rel='stylesheet', type='text/css')
        book.add_item(item)
        return item

    def _apply_footnote_links(self, text, token_occurrences, footnote_bodies, used_numbers):
        if not token_occurrences:
            return text, []

        token_to_html = {}
        fn_items = []
        for occ in token_occurrences:
            token = occ['token']
            if token in token_to_html:
                continue
            num = used_numbers[0] + 1
            used_numbers[0] = num
            target_id = occ['target_id']
            ref_id = f'fnref_{num}'
            fn_id = f'fn_{num}'
            link_html = f'<sup class="footnote-ref" id="{ref_id}"><a href="#{fn_id}">[{num}]</a></sup>'
            token_to_html[token] = link_html
            body = footnote_bodies.get(target_id, '')
            fn_items.append({'id': fn_id, 'ref_id': ref_id, 'body': body})

        for token, html_tag in token_to_html.items():
            text = text.replace(token, html_tag)
        return text, fn_items

    def _build_two_column_html(self, aligned_paras, header_row: str = "") -> str:
        rows = []
        if header_row:
            rows.append(header_row)
        for para in aligned_paras:
            for i, seg in enumerate(para):
                row_class = 'class="first-sentence"' if i == 0 else ''
                rows.append(
                    f'<tr {row_class}>'
                    f'<td class="bilingual-left">{seg["source"]}</td>'
                    f'<td class="bilingual-right">{seg["target"]}</td>'
                    f'</tr>'
                )
        return '<table class="bilingual-table">' + ''.join(rows) + '</table>'

    def _make_heading_html(self, path: List[str], prev_path: List[str]) -> str:
        common = 0
        for a, b in zip(path, prev_path):
            if a == b:
                common += 1
            else:
                break

        visible = path[common:]
        if not visible:
            visible = path

        lines = []
        for i, label in enumerate(visible):
            depth = common + i + 1
            level = min(depth, 6)
            escaped = html.escape(label)
            lines.append(f'<h{level} class="bilingual-heading">{escaped}</h{level}>')

        return '\n'.join(lines)

    def _build_chapter(self, source_info: Optional[Dict[str, Any]],
                       target_info: Optional[Dict[str, Any]],
                       alignment: List[List[Dict[str, str]]],
                       prev_source_path: List[str],
                       prev_target_path: List[str]) -> Dict[str, Any]:

        if source_info and target_info:
            src_display = source_info.get('display_path', [])
            tgt_display = target_info.get('display_path', [])
            src_toc = source_info.get('toc_path', [])
            tgt_toc = target_info.get('toc_path', [])

            src_heading = self._make_heading_html(src_display, prev_source_path)
            tgt_heading = self._make_heading_html(tgt_display, prev_target_path)

            header_row = (
                f'<tr class="title-row">'
                f'<td class="bilingual-left">{src_heading}</td>'
                f'<td class="bilingual-right">{tgt_heading}</td>'
                f'</tr>'
            )

            all_footnote_items = []
            used_numbers = [0]

            processed_paras = []
            for para in alignment:
                new_para = []
                for seg in para:
                    src_text = seg['source']
                    tgt_text = seg['target']
                    src_occurrences = seg.get('source_footnote_occurrences', [])
                    tgt_occurrences = seg.get('target_footnote_occurrences', [])

                    src_inlined = self._inline_html(src_text)
                    tgt_inlined = self._inline_html(tgt_text)

                    src_html, src_fns = self._apply_footnote_links(
                        src_inlined, src_occurrences, self.source_footnotes, used_numbers)
                    tgt_html, tgt_fns = self._apply_footnote_links(
                        tgt_inlined, tgt_occurrences, self.target_footnotes, used_numbers)

                    all_footnote_items.extend(src_fns)
                    all_footnote_items.extend(tgt_fns)

                    new_para.append({'source': src_html, 'target': tgt_html})
                processed_paras.append(new_para)

            body_html = self._build_two_column_html(processed_paras, header_row)

            if all_footnote_items:
                fn_list_items = ''.join(
                    f'<li id="{fn["id"]}">{fn["body"]} '
                    f'<a href="#{fn["ref_id"]}" class="footnote-backref">↩</a></li>'
                    for fn in all_footnote_items
                )
                body_html += (
                    '<hr class="footnote-separator"/>'
                    '<div class="footnotes"><ol>' + fn_list_items + '</ol></div>'
                )

            flat_title = src_display[-1] + ' / ' + tgt_display[-1]
            toc_path = tgt_toc

        elif source_info and not target_info:
            src_display = source_info.get('display_path', [])
            src_toc = source_info.get('toc_path', [])
            heading = self._make_heading_html(src_display, prev_source_path)
            body = self._paragraphs_html(source_info.get('text', ''))
            body_html = f'<div class="bilingual-source-only">\n{heading}\n{body}\n</div>'
            flat_title = src_display[-1] if src_display else ''
            toc_path = src_toc

        elif target_info and not source_info:
            tgt_display = target_info.get('display_path', [])
            tgt_toc = target_info.get('toc_path', [])
            heading = self._make_heading_html(tgt_display, prev_target_path)
            body = self._paragraphs_html(target_info.get('text', ''))
            body_html = f'<div class="bilingual-target-only">\n{heading}\n{body}\n</div>'
            flat_title = tgt_display[-1] if tgt_display else ''
            toc_path = tgt_toc

        else:
            body_html = flat_title = ''
            toc_path = []

        return {
            'body_html': body_html,
            'flat_title': flat_title,
            'toc_path': toc_path,
        }

    def run(self) -> epub.EpubBook | None:
        self.source_book = epub.read_epub(self.source_path)
        if not self.source_book:
            self.log.error(f"Failed to read source EPUB file {self.source_path}.")
            return None
        self.target_book = epub.read_epub(self.target_path)
        if not self.target_book:
            self.log.error(f"Failed to read target EPUB file {self.target_path}.")
            return None

        new_book = epub.EpubBook()
        self._copy_metadata(new_book)
        self._copy_cover(new_book)
        css_links = self._copy_styles(new_book)

        base_dir = self._get_base_dir()
        bilingual_css = epub.EpubItem(
            uid="bilingual_css",
            file_name=base_dir + "bilingual.css",
            media_type='text/css',
            content=b"""
            .bilingual-table {
                width: 100%;
                border-collapse: collapse;
                table-layout: fixed;
            }
            .bilingual-table td {
                display: table-cell !important;
                vertical-align: top;
                padding: 0.3em 1em;
                width: 50%;
            }
            tr.first-sentence td {
                text-indent: 1.5em;
            }
            .bilingual-heading {
                font-weight: bold;
                text-align: center;
                white-space: pre-line;
                margin: 0.5em 0;
            }
            .bilingual-table,
            .bilingual-table tr,
            .bilingual-table td,
            .bilingual-heading {
                border: 0 none transparent !important;
                border-style: none !important;
                border-width: 0 !important;
                border-color: transparent !important;
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
        new_book.add_item(bilingual_css)
        css_links.append(bilingual_css.file_name)

        new_spine_ids = []
        toc_entries = []

        last_source_path: List[str] = []
        last_target_path: List[str] = []

        with progress.phase('building', len(self.blocks), "Building chapters"):
            for block_idx, block in enumerate(self.blocks):
                source_info = block.get('source')
                target_info = block.get('target')
                if source_info is None and target_info is None:
                    continue

                alignment = block.get('alignment') or []

                ch = self._build_chapter(
                    source_info, target_info, alignment,
                    prev_source_path=last_source_path,
                    prev_target_path=last_target_path,
                )

                if source_info:
                    last_source_path = source_info.get('display_path', [])
                if target_info:
                    last_target_path = target_info.get('display_path', [])

                file_name = f"{base_dir}chap_{block_idx:03d}.xhtml"
                uid = f"chap_{block_idx:03d}"
                item = self._create_xhtml_item(
                    new_book, file_name, ch['flat_title'],
                    ch['body_html'], css_links, uid
                )
                new_spine_ids.append(item.get_id())
                toc_entries.append({
                    'file_name': item.file_name,
                    'uid': item.get_id(),
                    'toc_path': ch['toc_path'],
                })

                progress.update('building')

        new_book.add_item(epub.EpubNcx())
        new_book.add_item(epub.EpubNav())

        new_book.spine = new_spine_ids
        new_book.toc = self._build_toc(toc_entries)

        self.log.info("Book is ready!")
        return new_book