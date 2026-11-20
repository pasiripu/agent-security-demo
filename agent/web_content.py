"""Readable web content and byte-bounded model responses; no instruction filtering."""
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from .common import digest

MAX_TOOL_RESULT_BYTES = 12000
PAGE_CHARS = 6000


def github_content_url(url):
    parsed = urlsplit(url)
    parts = parsed.path.split('/')
    # Let GitHub resolve refs containing slashes, then follow its raw-content redirect.
    if parsed.hostname in ('github.com', 'www.github.com') and len(parts) >= 6 and parts[3] == 'blob':
        parts[3] = 'raw'
        return urlunsplit(('https', 'github.com', '/'.join(parts), '', ''))
    return url


class ReadableHTML(HTMLParser):
    OMIT = {'head', 'script', 'style', 'nav', 'footer', 'svg'}
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    BLOCK = {'p', 'div', 'section', 'article', 'main', 'header', 'li', 'ul', 'ol', 'pre',
             'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'br', 'hr'}

    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url, self.parts, self.skip, self.links = url, [], [], []

    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag not in self.VOID:
                self.skip.append(tag)
            return
        if tag in self.OMIT:
            self.skip.append(tag)
            return
        if tag in self.BLOCK:
            self.parts.append('\n')
        if tag in ('td', 'th'):
            self.parts.append('\t')
        if tag == 'a':
            try:
                href = urljoin(self.url, dict(attrs).get('href') or '')
                self.links.append(href if urlsplit(href).scheme in ('http', 'https') else '')
            except ValueError:
                self.links.append('')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag in self.skip:
                self.skip = self.skip[:len(self.skip) - 1 - self.skip[::-1].index(tag)]
            return
        if tag == 'a' and self.links:
            href = self.links.pop()
            if href:
                self.parts.append(' (' + href + ')')
        if tag in self.BLOCK:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', ''.join(self.parts)).strip()


def readable_text(text, content_type, url):
    mime = content_type.split(';', 1)[0].strip().lower()
    if mime not in ('text/html', 'application/xhtml+xml'):
        return text, 'text'
    parser = ReadableHTML(url)
    parser.feed(text)
    parser.close()
    return parser.text(), 'readable-text'


def content_page(document, offset):
    """Bound the complete JSON text, including escaping and URL metadata."""
    text = document['body']
    if type(offset) is not int or offset < 0 or offset > len(text):
        raise ValueError('offset must be a character position within the retrieved content')
    page = {k: v for k, v in document.items() if k not in ('body', 'sha256', 'redirects')}
    # Full addresses/redirects remain in the HTTP audit; avoid metadata alone filling a result.
    for key in ('url', 'final_url'):
        if len(page.get(key, '')) > 512:
            page[key] = page[key][:509] + '...'
            page['url_metadata_shortened'] = True
    page['content_type'] = str(page.get('content_type', ''))[:120]
    page['redirect_count'] = len(document.get('redirects', []))
    page['content_sha256'] = document['sha256']
    page['offset'], page['total_characters'] = offset, len(text)

    def candidate(length):
        body = text[offset:offset + length]
        end = offset + len(body)
        return dict(page, body=body, sha256=digest(body),
                    next_offset=end if end < len(text) else None, has_more=end < len(text))

    low, high = 0, min(PAGE_CHARS, len(text) - offset)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(candidate(middle)).encode('utf-8')) <= MAX_TOOL_RESULT_BYTES:
            low = middle
        else:
            high = middle - 1
    result = candidate(low)
    if len(json.dumps(result).encode('utf-8')) > MAX_TOOL_RESULT_BYTES or (offset < len(text) and not low):
        raise ValueError('Response metadata exceeds the tool response budget')
    return result
