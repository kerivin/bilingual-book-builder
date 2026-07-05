import os
import uuid
from typing import List, Dict, Any, Optional
from fast_ebook import epub
import fast_ebook

class BookBuilder:
    def __init__(self, target_book: epub.EpubBook, blocks: List[Dict[str, Any]]):
        self.target_book = target_book
        self.blocks = blocks

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
        for item in self.target_book.get_items():
            if item.get_type() == epub.ITEM_COVER:
                new_book.add_item(epub.EpubImage(
                    uid=item.get_id(),
                    file_name=item.get_name(),
                    media_type=item.get_media_type(),
                    content=item.get_content()
                ))
                break

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

    def _copy_other_items(self, new_book: epub.EpubBook) -> None:
        spine_ids = {idref for idref, _ in self.target_book.get_spine()}
        for item in self.target_book.get_items():
            if item.get_id() in spine_ids and item.get_type() == epub.ITEM_DOCUMENT:
                continue
            if item.get_type() in (epub.ITEM_COVER, epub.ITEM_STYLE, epub.ITEM_NAVIGATION):
                continue
            # fonts, scripts, etc. – not critical
            pass

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

        # Metadata
        identifiers = self.target_book.get_metadata('DC', 'identifier')
        if identifiers:
            new_book.set_identifier(identifiers[0][0])
        titles = self.target_book.get_metadata('DC', 'title')
        if titles:
            new_book.set_title(titles[0][0])
        languages = self.target_book.get_metadata('DC', 'language')
        if languages:
            new_book.set_language(languages[0][0])
        for creator in self.target_book.get_metadata('DC', 'creator'):
            attrs = creator[1] if len(creator) > 1 else {}
            new_book.add_author(creator[0], **attrs)
        for publisher in self.target_book.get_metadata('DC', 'publisher'):
            new_book.add_metadata('DC', 'publisher', publisher[0])
        for date in self.target_book.get_metadata('DC', 'date'):
            new_book.add_metadata('DC', 'date', date[0])
        for key in ('description', 'subject', 'contributor', 'rights'):
            for val in self.target_book.get_metadata('DC', key):
                new_book.add_metadata('DC', key, val[0])

        # Copy cover and styles
        self._copy_cover(new_book)
        css_links = self._copy_styles(new_book)
        self._copy_other_items(new_book)

        # Add our two‑column CSS (table-based, transparent split, strong override)
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
                margin: 0.5em 0; 
            }
            """
        )
        new_book.add_item(col_css)
        css_links.append(base_dir + "columns.css")

        new_spine_ids = []
        toc_links = []

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
                # print(f"Building {source_title} - {target_title}...")
                header_row = f'<tr class="title-row"><td class="col-left"><h2 class="col-heading">{source_title}</h2></td><td class="col-right"><h2 class="col-heading">{target_title}</h2></td></tr>'
                body_html = self._build_two_column_html(alignment, header_row)
                item = self._create_xhtml_item(new_book, file_name, f"{source_title} / {target_title}", body_html, css_links, uid)
                new_spine_ids.append(item.get_id())
                toc_links.append(epub.Link(item.get_name(), target_title, item.get_id()))

            elif source_info is not None:
                title = source_info.get('title', f'Chapter {block_idx}')
                # print(f"Building source {title}...")
                body = self._text_to_paragraphs(source_info.get('text', ''))
                item = self._create_xhtml_item(new_book, file_name, title, f'<div class="source-only">{body}</div>', css_links, uid)
                new_spine_ids.append(item.get_id())
                toc_links.append(epub.Link(item.get_name(), title, item.get_id()))

            elif target_info is not None:
                title = target_info.get('title', f'Chapter {block_idx}')
                # print(f"Building target {title}...")
                body = self._text_to_paragraphs(target_info.get('text', ''))
                item = self._create_xhtml_item(new_book, file_name, title, f'<div class="target-only">{body}</div>', css_links, uid)
                new_spine_ids.append(item.get_id())
                toc_links.append(epub.Link(item.get_name(), title, item.get_id()))

        # Add navigation
        new_book.add_item(epub.EpubNcx())
        new_book.add_item(epub.EpubNav())

        # Set spine and TOC
        new_book.spine = new_spine_ids
        new_book.toc = toc_links

        return new_book