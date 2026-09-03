"""
Text normalisation for search.

Arabic poetry is stored fully vocalised (بِحَقِّ اللهِ) but is almost always typed
without vowel marks (بحق الله), so a byte-exact match finds nothing. Both the
indexed text and the incoming query are folded through the same normalisation
so the two forms meet.
"""

import re
import unicodedata

# Harakat, quranic annotation marks, and tatweel (the kashida stretch).
DIACRITICS_RE = re.compile(
    '['
    'ً-ْ'  # fathatan..sukun
    'ٓ-ٕ'  # maddah, hamza above/below
    'ٰ'         # superscript alef
    'ۖ-ۭ'  # quranic annotation
    'ـ'         # tatweel
    ']'
)

# Letter forms a reader treats as the same letter when searching.
LETTER_FOLDING = str.maketrans({
    'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا',
    'ى': 'ي', 'ئ': 'ي',
    'ؤ': 'و',
    'ة': 'ه',
    'ﷲ': 'الله',
})

PUNCTUATION_RE = re.compile(r'[^\w\s؀-ۿ]+', re.UNICODE)
WHITESPACE_RE = re.compile(r'\s+')


def normalize(text):
    """Fold text into the form used for both indexing and querying."""
    if not text:
        return ''
    text = unicodedata.normalize('NFKC', text)
    text = DIACRITICS_RE.sub('', text)
    text = text.translate(LETTER_FOLDING)
    text = PUNCTUATION_RE.sub(' ', text)
    return WHITESPACE_RE.sub(' ', text).strip().lower()


def build_document(*fields):
    """Join the searchable fields of a qasida into one normalised document."""
    return normalize(' \n '.join(f for f in fields if f))


TOKEN_RE = re.compile(r'[\w؀-ۿ]+', re.UNICODE)


def to_tsquery(query):
    """
    Turn a user query into a prefix tsquery string.

    Prefix matching ("burd:*" finding "burdah") is what makes a partial word
    usable while still going through the index, so the terms are ANDed with a
    trailing ':*' each. Returns '' when there is nothing searchable.
    """
    tokens = TOKEN_RE.findall(normalize(query))
    return ' & '.join(f'{t}:*' for t in tokens if t)
