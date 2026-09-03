"""
Generic extraction, for pointing the crawler at a site with no bespoke parser.

Order of preference, most reliable first:

1. schema.org JSON-LD (MusicComposition / CreativeWork / Article). Sites that
   publish it give us the title, author, language and the lyric text with its
   line structure already correct.
2. The densest block of text in the page body, with <br> read as a line break
   and block boundaries as stanza breaks - which is how these pages encode
   verse structure.

Everything returns plain data so the caller decides what to persist.
"""

import json
import re

from bs4 import BeautifulSoup

ARABIC_RE = re.compile(r'[؀-ۿ]')

# Elements that never contain verse.
JUNK_SELECTORS = (
    'script', 'style', 'nav', 'header', 'footer', 'aside', 'form', 'noscript',
    'iframe', '.sharedaddy', '.wpcnt', '.related', '.comments', '#comments',
    '.sidebar', '.widget', '.advert', '.ad', '.breadcrumb', '.menu',
    # Google's translate widget renders its whole language list into the page,
    # which is dense enough to be mistaken for the main text.
    '#google_translate_element', '.goog-te-menu-frame', '.goog-te-gadget',
    '.skiptranslate', '[class*="goog-te"]', 'select', 'option', 'datalist',
)

# Names of languages, as a translate picker lists them. A block that is mostly
# these is a menu, not a poem.
LANGUAGE_MENU_WORDS = frozenset({
    'afrikaans', 'albanian', 'amharic', 'arabic', 'armenian', 'azerbaijani',
    'basque', 'belarusian', 'bengali', 'bosnian', 'bulgarian', 'catalan',
    'corsican', 'croatian', 'czech', 'danish', 'dutch', 'english', 'esperanto',
    'estonian', 'finnish', 'french', 'galician', 'georgian', 'german', 'greek',
    'gujarati', 'haitian', 'hausa', 'hindi', 'hungarian', 'icelandic', 'igbo',
    'indonesian', 'irish', 'italian', 'japanese', 'javanese', 'kannada',
    'kazakh', 'khmer', 'korean', 'kurdish', 'kyrgyz', 'latin', 'latvian',
    'lithuanian', 'luxembourgish', 'macedonian', 'malagasy', 'malay',
    'malayalam', 'maltese', 'maori', 'marathi', 'mongolian', 'nepali',
    'norwegian', 'nyanja', 'occitan', 'oriya', 'oromo', 'pashto', 'persian',
    'polish', 'portuguese', 'punjabi', 'romanian', 'russian', 'samoan',
    'serbian', 'sesotho', 'shona', 'sindhi', 'sinhala', 'slovak', 'slovenian',
    'somali', 'spanish', 'sundanese', 'swahili', 'swedish', 'tajik', 'tamil',
    'tatar', 'telugu', 'thai', 'tibetan', 'tigrinya', 'turkish', 'turkmen',
    'ukrainian', 'urdu', 'uyghur', 'uzbek', 'vietnamese', 'welsh', 'wolof',
    'xhosa', 'yiddish', 'yoruba', 'zulu',
})


def _is_language_menu(text):
    """True when a block is mostly language names run together."""
    lowered = (text or '').lower()
    hits = sum(1 for name in LANGUAGE_MENU_WORDS if name in lowered)
    return hits >= 12

LD_TYPES = ('MusicComposition', 'CreativeWork', 'Article', 'BlogPosting', 'WebPage')

# A candidate block needs this much text before it is treated as verse.
MIN_VERSE_CHARS = 120
MIN_VERSE_LINES = 4


def _clean_soup(soup):
    for selector in JUNK_SELECTORS:
        for node in soup.select(selector):
            node.decompose()
    return soup


BLOCK_TAGS = ('p', 'div', 'li', 'section', 'article', 'pre', 'tr', 'td', 'span')


def _leaf_blocks(element):
    """
    Elements holding one run of text and no block children of their own.

    These are the verse lines. Looking at an element's direct children instead
    fails on pages that nest the lines several wrappers deep - one site puts
    each line in its own div inside 1400 others, and reading the wrappers
    turned a whole poem into a single line.
    """
    leaves = []
    for node in element.find_all(BLOCK_TAGS):
        if node.find(BLOCK_TAGS):
            continue  # a wrapper, not a line
        text = node.get_text(' ', strip=True)
        if text:
            leaves.append(node)
    return leaves


def _lines_from_breaks(element):
    """Split an element on <br>, which is the other way pages mark lines."""
    lines, current = [], []
    for node in element.descendants:
        if getattr(node, 'name', None) == 'br':
            lines.append(''.join(current).strip())
            current = []
        elif isinstance(node, str):
            current.append(node)
    lines.append(''.join(current).strip())
    return [line for line in lines if line]


def html_to_verse(element):
    """
    Render an element as verse text.

    Lines come from the leaf elements that hold them, or from <br> where the
    page uses that instead. Stanzas follow the leaves' parents, so the grouping
    the page displays survives into storage.
    """
    leaves = _leaf_blocks(element)

    if len(leaves) > 1:
        stanzas, current_parent, current = [], None, []
        for leaf in leaves:
            text = leaf.get_text(' ', strip=True)
            if not text:
                continue
            parent = leaf.parent
            if current and parent is not current_parent:
                stanzas.append('\n'.join(current))
                current = []
            current_parent = parent
            # A leaf may still carry <br> inside it.
            pieces = _lines_from_breaks(leaf) or [text]
            current.extend(pieces)
        if current:
            stanzas.append('\n'.join(current))
        rendered = '\n\n'.join(block for block in stanzas if block.strip())
    else:
        rendered = '\n'.join(_lines_from_breaks(element))

    return re.sub(r'\n{3,}', '\n\n', rendered).strip()


def json_ld_objects(soup):
    """Every JSON-LD object on the page, flattened."""
    found = []
    for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
        try:
            data = json.loads(script.string or '')
        except (ValueError, TypeError):
            continue
        queue = data if isinstance(data, list) else [data]
        while queue:
            item = queue.pop()
            if isinstance(item, dict):
                found.append(item)
                for value in item.values():
                    if isinstance(value, (dict, list)):
                        queue.append(value)
            elif isinstance(item, list):
                queue.extend(item)
    return found


def _as_text(value):
    """JSON-LD fields arrive as a string, a dict with `text`, or a list."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _as_text(value.get('text') or value.get('name') or '')
    if isinstance(value, list) and value:
        return _as_text(value[0])
    return ''


def from_json_ld(soup):
    """Pull a work out of JSON-LD, or return None."""
    for item in json_ld_objects(soup):
        if item.get('@type') not in LD_TYPES:
            continue
        lyrics = _as_text(item.get('lyrics') or item.get('articleBody') or '')
        if len(lyrics) < MIN_VERSE_CHARS:
            continue
        translation = item.get('workTranslation') or {}
        return {
            'title': _as_text(item.get('name') or item.get('headline')),
            'lyrics': lyrics,
            'author': _as_text(item.get('lyricist') or item.get('author')),
            'language': _as_text(item.get('inLanguage')),
            'genre': _as_text(item.get('genre')),
            'translation_url': (translation.get('url') or '') if isinstance(translation, dict) else '',
            'via': 'json-ld',
        }
    return None


def from_markup(soup):
    """Fall back to the densest block of text in the page."""
    best, best_score = None, 0
    for element in soup.find_all(['article', 'div', 'section', 'pre', 'td']):
        text = element.get_text('\n', strip=True)
        if len(text) < MIN_VERSE_CHARS:
            continue
        if _is_language_menu(text):
            continue
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < MIN_VERSE_LINES:
            continue
        # Prefer many short lines (verse) over few long ones (prose), and
        # penalise containers that merely wrap the real one.
        average = sum(len(line) for line in lines) / len(lines)
        score = len(lines) * max(0, 90 - min(average, 90))
        score -= len(element.find_all(['article', 'section'])) * 40
        if score > best_score:
            best, best_score = element, score

    if best is None:
        return None

    heading = soup.find(['h1', 'h2'])
    return {
        'title': heading.get_text(strip=True) if heading else '',
        'lyrics': html_to_verse(best),
        'author': '',
        'language': '',
        'genre': '',
        'translation_url': '',
        'via': 'markup',
    }


def _looks_like_verse(text):
    """
    Verse is either in a non-Latin script or laid out as many short lines.

    Only applied to the markup fallback: JSON-LD naming something as lyrics is
    taken at its word.
    """
    if ARABIC_RE.search(text):
        return True
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 6:
        return False
    average = sum(len(l) for l in lines) / len(lines)
    return average < 70


def extract_work(html):
    """
    Extract one work from a page. Returns a dict or None.

    JSON-LD is tried first because it is unambiguous; the markup heuristic is
    only reached for pages that publish no structured data.
    """
    soup = BeautifulSoup(html, 'html.parser')
    # JSON-LD must be read before the junk pass, which strips <script> and
    # would otherwise remove the structured data along with it.
    work = from_json_ld(soup)
    if work is None:
        work = from_markup(_clean_soup(soup))
    if work and _is_language_menu(work['lyrics']):
        # The densest text on the page was a translate menu.
        return None
    if work and work['via'] == 'markup' and not _looks_like_verse(work['lyrics']):
        # An About or privacy page is the densest text on some sites; without
        # this check they get stored as qasidas.
        return None
    if not work or len(work['lyrics']) < MIN_VERSE_CHARS:
        return None
    if not work['title']:
        title_tag = soup.find('title')
        work['title'] = title_tag.get_text(strip=True) if title_tag else ''
    return work
