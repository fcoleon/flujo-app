from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse


class DescriptionSanitizer(HTMLParser):
    allowed = {'p','div','br','strong','b','em','i','u','s','ul','ol','li','blockquote','h2','h3','a'}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed: return
        if tag == 'a':
            href=dict(attrs).get('href','').strip()
            if urlparse(href).scheme.lower() not in ('http','https','mailto'): self.parts.append('<a>'); return
            self.parts.append('<a href="'+escape(href,quote=True)+'" target="_blank" rel="noopener noreferrer">'); return
        self.parts.append('<'+tag+'>')
    def handle_endtag(self, tag):
        if tag in self.allowed and tag != 'br': self.parts.append('</'+tag+'>')
    def handle_data(self, data): self.parts.append(escape(data))

def sanitize_description(value):
    parser=DescriptionSanitizer(); parser.feed(value); parser.close(); return ''.join(parser.parts)
