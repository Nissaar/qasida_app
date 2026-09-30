"""
Which language a text is in, from the letters it is written with.

Arabic, Urdu and Persian share one script, so telling them apart by script
alone is not possible, and a general-purpose detector is a large download for
a question with three answers. The three spell differently enough to decide
it from the letters:

- Urdu has letters of its own - ٹ ڈ ڑ ں ے ھ ہ - that the other two never use;
- Arabic writes ي and ك and ends many words in ة, and a large share of its
  words open with the article ال;
- Persian writes ی and ک, the same sounds in its own letter forms, and uses
  پ چ ژ گ, which Arabic does not (Urdu does too, but is caught first).

Latin-script text is either English, which needs no translating, or a
romanisation, which the models cannot read.
"""

import re

ARABIC_SCRIPT_RE = re.compile(r'[؀-ۿ]')
LATIN_RE = re.compile(r'[A-Za-z]')
WORD_RE = re.compile(r'[؀-ۿ]+')

URDU_ONLY = set('ٹڈڑںےۓھہ')   # ٹ ڈ ڑ ں ے ۓ ھ ہ
ARABIC_FORMS = set('يكةى')                        # ي ك ة ى
PERSIAN_FORMS = set('یک')                                   # ی ک
NOT_ARABIC = set('پچژگ')                           # پ چ ژ گ

ENGLISH_WORDS = {'the', 'and', 'of', 'to', 'is', 'in', 'you', 'my', 'your', 'our',
                 'we', 'he', 'his', 'o', 'lord', 'with', 'for', 'all'}

NAMES = {'ar': 'Arabic', 'ur': 'Urdu', 'fa': 'Persian', 'en': 'English'}

# Below this many letters there is too little to judge.
MIN_LETTERS = 12


def detect(text):
    """
    (code, name, how) for the text's language.

    code is 'ar', 'ur', 'fa' or 'en'; 'latin' for a romanisation; None when
    there is too little text to say. `how` is a short reason, shown to the
    editor so a wrong guess can be understood and overridden.
    """
    text = text or ''
    native = len(ARABIC_SCRIPT_RE.findall(text))
    latin = len(LATIN_RE.findall(text))
    if native + latin < MIN_LETTERS:
        return None, None, 'there is too little text to tell'

    if latin > native:
        words = re.findall(r'[a-z]+', text.lower())
        english = sum(1 for word in words if word in ENGLISH_WORDS)
        if words and english / len(words) > 0.12:
            return 'en', 'English', 'it is already in English'
        return 'latin', None, 'it is written in Latin letters (a transliteration)'

    urdu = sum(1 for c in text if c in URDU_ONLY)
    arabic = sum(1 for c in text if c in ARABIC_FORMS)
    persian = sum(1 for c in text if c in PERSIAN_FORMS) + sum(1 for c in text if c in NOT_ARABIC)
    words = WORD_RE.findall(text)
    article = sum(1 for word in words if word.startswith('ال') and len(word) > 3)
    article_share = article / len(words) if words else 0

    if urdu >= max(2, native * 0.01):
        return 'ur', 'Urdu', 'it uses letters only Urdu has (ٹ ڈ ڑ ں ے)'
    if persian > arabic and article_share <= 0.12:
        return 'fa', 'Persian', 'it uses Persian letter forms (ی ک گ) and no Urdu letters'
    return 'ar', 'Arabic', 'it uses Arabic letter forms and the article ال'
