import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

@dataclass
class Token:
    kind: str          # 'open', 'close', 'text', 'void'
    content: str
    length: int = 0    # only meaningful for text


VOID_ELEMENTS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr'
}


def _is_void(tag_name: str) -> bool:
    return tag_name.lower() in VOID_ELEMENTS


def tokenize(element) -> List[Token]:
    tokens: List[Token] = []
    _walk(element, tokens)
    return tokens


def tokenize_children(element) -> List[Token]:
    tokens: List[Token] = []
    if hasattr(element, 'children'):
        for child in element.children:
            _walk(child, tokens)
    return tokens


def _walk(node, tokens: List[Token]):
    if isinstance(node, str):
        text = str(node)
        if text:
            tokens.append(Token('text', text, length=len(text)))
        return
    if not hasattr(node, 'name'):
        return
    tag = node.name
    if tag in ('script', 'style', 'img', 'figure', 'svg', 'canvas'):
        return
    attrs = node.attrs if hasattr(node, 'attrs') else {}
    attr_parts = []
    for k, v in attrs.items():
        if v is None:
            attr_parts.append(k)
        else:
            attr_parts.append(f'{k}="{v}"')
    open_tag = f'<{tag}'
    if attr_parts:
        open_tag += ' ' + ' '.join(attr_parts)
    if _is_void(tag):
        open_tag += '/>'
        tokens.append(Token('void', open_tag))
        return
    open_tag += '>'
    tokens.append(Token('open', open_tag))
    for child in node.children:
        _walk(child, tokens)
    tokens.append(Token('close', f'</{tag}>'))


def rebuild_sentence(tokens: List[Token],
                     start_char: int,
                     end_char: int,
                     open_tags: Optional[List[str]] = None) -> Tuple[str, List[str]]:
    if open_tags is None:
        open_tags = []
    current_tag_stack = list(open_tags)
    pos = 0
    html_parts = []
    tag_open = False
    for tok in tokens:
        if tok.kind == 'text':
            tok_start = pos
            tok_end = pos + tok.length
            if tok_start < end_char and tok_end > start_char:
                if not tag_open:
                    for tag in current_tag_stack:
                        html_parts.append(tag)
                    tag_open = True
                text = tok.content
                if tok_start >= start_char and tok_end <= end_char:
                    html_parts.append(text)
                else:
                    if tok_start < start_char:
                        text = text[start_char - tok_start:]
                    if tok_end > end_char:
                        text = text[:end_char - tok_end]
                    html_parts.append(text)
            pos = tok_end
        elif tok.kind == 'open':
            if tag_open or pos >= start_char:
                if not tag_open:
                    for tag in current_tag_stack:
                        html_parts.append(tag)
                    tag_open = True
                html_parts.append(tok.content)
            current_tag_stack.append(tok.content)
        elif tok.kind == 'close':
            if tag_open or pos >= start_char:
                if not tag_open:
                    for tag in current_tag_stack:
                        html_parts.append(tag)
                    tag_open = True
                html_parts.append(tok.content)
            if current_tag_stack:
                current_tag_stack.pop()
        elif tok.kind == 'void':
            if tag_open or pos >= start_char:
                if not tag_open:
                    for tag in current_tag_stack:
                        html_parts.append(tag)
                    tag_open = True
                html_parts.append(tok.content)
        if tag_open and pos >= end_char:
            break
    if tag_open:
        remaining_stack = list(current_tag_stack)
        close_tags = [re.sub(r'^<(\w+)', r'</\1', t) for t in reversed(remaining_stack)]
        html_parts.extend(close_tags)
    return ''.join(html_parts), current_tag_stack