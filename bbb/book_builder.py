import os
import uuid
from typing import List, Dict, Any, Optional
from fast_ebook import epub
import fast_ebook
import logging
from bbb import progress

class BookBuilder:
    def __init__(self, source_book: epub.EpubBook, target_book: epub.EpubBook, blocks: List[Dict[str, Any]]):
        self.source_book = source_book
        self.target_book = target_book
        self.blocks = blocks
        self.log = logging.getLogger(__name__)

    def _get_base_dir(self) -> str:
        spine = self.target_book.get_spine()
        if not spine:
            return "text/"
        first_id = spine[0][0]
        item = self.target_book.get_item_with_id(first_id)
        if item is None:
            return "text/"
        href = item.get_name()
        return os.path.dirname(href) + "/" if os.path.dirname(href) else ""

    def _copy_cover(self, new_book: epub.EpubBook) -> None:
        cover_id = None
        for meta in self.target_book.get_metadata('', 'meta'):
            if isinstance(meta, tuple) and meta[1].get('name') == 'cover':
                cover_id = meta[1].get('content')
                break
            elif isinstance(meta, dict) and meta.get('name') == 'cover':
                cover_id = meta.get('content')
                break

        cover_item = None
        if cover_id:
            cover_item = self.target_book.get_item_with_id(cover_id)

        if not cover_item:
            for item in self.target_book.get_items():
                if item.get_type() == epub.ITEM_COVER:
                    cover_item = item
                    break

        if not cover_item:
            for item in self.target_book.get_items():
                if item.get_type() == epub.ITEM_IMAGE and 'cover' in item.get_name().lower():
                    cover_item = item
                    break

        if cover_item:
            new_book.set_cover(cover_item.get_name(), cover_item.get_content())

    def _copy_styles(self, new_book: epub.EpubBook) -> List[str]:
        css_links = []
        for item in self.target_book.get_items():
            if item.get_type() == epub.ITEM_STYLE:
                css = epub.EpubCss(
                    uid=item.get_id(),
                    file_name=item.get_name(),
                    content=item.get_content()
                )
                new_book.add_item(css)
                css_links.append(item.get_name())
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
                    authors += "creator[0], "
                except TypeError:
                    new_book.add_author(creator[0])
                    authors += "creator[0], "
            
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

    def _create_xhtml_item(self, book: epub.EpubBook, file_name: str, title: str, body_html: str, css_links: List[str], uid: Optional[str] = None):
        if uid is None:
            uid = f"chap_{uuid.uuid4().hex[:8]}"
        html = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <meta charset="utf-8"/>
    <title>{title}</title>
"""
        for css_path in css_links:
            html += f'    <link rel="stylesheet" type="text/css" href="{css_path}"/>\n'
        html += f"""</head>
<body>
{body_html}
</body>
</html>"""
        item = epub.EpubHtml(uid=uid, file_name=file_name, media_type='application/xhtml+xml', content=html.encode('utf-8'))
        book.add_item(item)
        return item

    def _text_to_paragraphs(self, text: str) -> str:
        if not text:
            return ""
        paragraphs = text.split('\n\n')
        return '\n'.join(f'<p>{" ".join(p.split())}</p>' for p in paragraphs if p.strip())

    def _build_two_column_html(self, alignment: List[Dict[str, str]], header_row: str = "") -> str:
        rows = []
        if header_row:
            rows.append(header_row)
        for seg in alignment:
            source_text = self._text_to_paragraphs(seg.get('source', ''))
            target_text = self._text_to_paragraphs(seg.get('target', ''))
            rows.append(f'<tr><td class="col-left">{source_text}</td><td class="col-right">{target_text}</td></tr>')
        return '<table class="two-column-table">' + ''.join(rows) + '</table>'

    def run(self) -> epub.EpubBook:
        new_book = epub.EpubBook()

        self._copy_metadata(new_book)
        self._copy_cover(new_book)
        css_links = self._copy_styles(new_book)

        base_dir = self._get_base_dir()
        col_css = epub.EpubCss(
            uid="columns_css",
            file_name=base_dir + "columns.css",
            content=b"""
            .two-column-table {
                width: 100%;
                border-collapse: collapse;
                table-layout: fixed;
            }
            .two-column-table td {
                display: table-cell !important;
                vertical-align: top;
                padding: 0.3em 1em;
                width: 50%;
            }
            .col-heading {
                font-weight: bold;
                text-align: center;
                white-space: pre-wrap;
                margin: 0.5em 0;
            }
            .two-column-table,
            .two-column-table tr,
            .two-column-table td,
            .col-heading {
                border: 0 none transparent !important;
                border-style: none !important;
                border-width: 0 !important;
                border-color: transparent !important;
            }
            """
        )
        new_book.add_item(col_css)
        css_links.append(base_dir + "columns.css")

        new_spine_ids = []
        toc_links = []

        with progress.phase('building', len(self.blocks), "Building chapters"):
            for block_idx, block in enumerate(self.blocks):
                source_info = block.get('source')
                target_info = block.get('target')

                if source_info is None and target_info is None:
                    continue

                alignment = block.get('alignment') or []
                file_name = f"{base_dir}chap_{block_idx:03d}.xhtml"
                uid = f"chap_{block_idx:03d}"

                if source_info is not None and target_info is not None:
                    source_title = source_info.get('title', 'Source')
                    target_title = target_info.get('title', 'Target')
                    self.log.info(f"Building {source_title} - {target_title}...")

                    header_row = f'<tr class="title-row"><td class="col-left"><h2 class="col-heading">{source_title}</h2></td><td class="col-right"><h2 class="col-heading">{target_title}</h2></td></tr>'
                    body_html = self._build_two_column_html(alignment, header_row)
                    item = self._create_xhtml_item(new_book, file_name, f"{source_title} / {target_title}", body_html, css_links, uid)
                    new_spine_ids.append(item.get_id())

                    toc_title = target_info.get('toc_title', target_title.replace('\n', ' '))
                    toc_links.append(epub.Link(item.get_name(), toc_title, item.get_id()))

                elif source_info is not None:
                    title = source_info.get('title', f'Chapter {block_idx}')
                    self.log.info(f"Building source {title}...")

                    body = self._text_to_paragraphs(source_info.get('text', ''))
                    item = self._create_xhtml_item(new_book, file_name, title, f'<div class="source-only">{body}</div>', css_links, uid)
                    new_spine_ids.append(item.get_id())

                    toc_title = source_info.get('toc_title', title.replace('\n', ' '))
                    toc_links.append(epub.Link(item.get_name(), title, item.get_id()))

                elif target_info is not None:
                    title = target_info.get('title', f'Chapter {block_idx}')
                    self.log.info(f"Building target {title}...")

                    body = self._text_to_paragraphs(target_info.get('text', ''))
                    item = self._create_xhtml_item(new_book, file_name, title, f'<div class="target-only">{body}</div>', css_links, uid)
                    new_spine_ids.append(item.get_id())

                    toc_title = target_info.get('toc_title', title.replace('\n', ' '))
                    toc_links.append(epub.Link(item.get_name(), toc_title, item.get_id()))
                
                progress.update('building')

        new_book.add_item(epub.EpubNcx())
        new_book.add_item(epub.EpubNav())

        new_book.spine = new_spine_ids
        new_book.toc = toc_links

        self.log.info("Book is ready!")
        return new_book