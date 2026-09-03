"""
Splitting source titles into their parts.

damas publishes a single title string that packs three fields together, e.g.

    Qasida | Ithna 'Ashara - القصيدة الاثنى عشرة | Sh. Muhammad al-Yaqoubi
    ^form    ^transliteration  ^arabic title        ^author

Other sources supply a plain title, which must pass through untouched.
"""

import re

# Leading words that name the form rather than the poem; these already exist as
# tags, so they are dropped from the title.
FORM_WORDS = {
    'qasida', 'qasidah', 'naat', 'nasheed', 'nashid', 'mawlid', 'manzhuma',
    'manzuma', 'madih', 'hadra', 'burda', 'burdah', 'poem', 'poetry',
}

# Marks that introduce a person in these titles.
AUTHOR_HINTS = (
    'sh.', 'shaykh', 'sheikh', 'imam', 'sayyid', 'sayyidi', 'sidi', 'mawlana',
    'al-qutb', 'ibn ', 'hazrat', 'dr.', 'prof.',
)

ARABIC_RE = re.compile(r'[؀-ۿ]')
# En dash, em dash, or a hyphen with spaces around it - the transliteration and
# the Arabic title are separated by one of these.
TITLE_SPLIT_RE = re.compile(r'\s+[–—]\s+|\s+-\s+')


def _looks_like_author(part):
    lowered = part.lower()
    if any(hint in lowered for hint in AUTHOR_HINTS):
        return True
    # "Al-Qutb Abu Madyan", "Ibn al-Farid" - a short name-shaped fragment with
    # the Arabic definite article and no Arabic script.
    return bool(re.match(r'^(al-|abu |abd )', lowered)) and len(part.split()) <= 5


def _split_scripts(text):
    """
    Split a mixed Latin/Arabic string where the script changes.

    This is more reliable than looking for a separator: these titles variously
    use an en dash, a spaced hyphen, an unspaced hyphen or another pipe, and
    the script boundary is present regardless.
    """
    match = ARABIC_RE.search(text)
    if not match:
        return text.strip(' -–—:|'), ''
    latin = text[:match.start()].strip(' -–—:|\t')
    arabic = text[match.start():].strip(' -–—:|\t')
    return latin, arabic


def split_title(raw):
    """
    Return (title, arabic_title, author) for a source title string.

    A title with nothing to separate comes back as-is with the other two fields
    empty, so plain titles from the other sources pass through untouched.
    """
    raw = (raw or '').strip()
    if not raw:
        return '', '', ''

    parts = [p.strip(' \t\u200f') for p in raw.split('|')]
    parts = [p for p in parts if p]

    author = ''
    if len(parts) > 1 and _looks_like_author(parts[-1]) and not ARABIC_RE.search(parts[-1]):
        author = parts.pop()

    # Drop a leading form word only when something else remains.
    if len(parts) > 1 and parts[0].rstrip(':').lower() in FORM_WORDS:
        parts.pop(0)

    body = ' | '.join(parts).strip()
    latin, arabic = _split_scripts(body)

    if latin and arabic:
        return latin, arabic, author
    if arabic:
        # Wholly Arabic: it is the title, so do not duplicate it.
        return arabic, '', author
    return latin or body, '', author
