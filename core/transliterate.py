"""
Rule-based romanisation of Arabic and Urdu script.

A caveat worth stating plainly: Arabic and Urdu are normally written without
short vowels, and those vowels cannot be recovered from the letters alone -
that needs a language model, not a mapping. So where the text carries harakat
this produces a fair romanisation, and where it does not the output is a
consonant skeleton. It is meant as a first draft for an editor to correct, not
as a finished transliteration.
"""

import re
import unicodedata

# Consonants, including the letters Urdu adds to the Arabic set.
CONSONANTS = {
    'ا': '', 'ب': 'b', 'پ': 'p', 'ت': 't', 'ٹ': 'ṭ', 'ث': 'th', 'ج': 'j',
    'چ': 'ch', 'ح': 'h', 'خ': 'kh', 'د': 'd', 'ڈ': 'ḍ', 'ذ': 'dh', 'ر': 'r',
    'ڑ': 'ṛ', 'ز': 'z', 'ژ': 'zh', 'س': 's', 'ش': 'sh', 'ص': 's', 'ض': 'd',
    'ط': 't', 'ظ': 'z', 'ع': "'", 'غ': 'gh', 'ف': 'f', 'ق': 'q', 'ك': 'k',
    'ک': 'k', 'گ': 'g', 'ل': 'l', 'م': 'm', 'ن': 'n', 'ں': 'n', 'ه': 'h',
    'ہ': 'h', 'ھ': 'h', 'و': 'w', 'ی': 'y', 'ي': 'y', 'ے': 'e', 'ئ': "'",
    'ء': "'", 'أ': 'a', 'إ': 'i', 'آ': 'a', 'ؤ': 'u', 'ة': 'h', 'ى': 'a',
}

# Short vowels, doubling and case endings.
HARAKAT = {
    'َ': 'a', 'ِ': 'i', 'ُ': 'u', 'ً': 'an', 'ٍ': 'in', 'ٌ': 'un',
    'ْ': '', 'ٰ': 'a',
}
SHADDA = 'ّ'
TATWEEL = 'ـ'

# Long vowels: a consonant followed by one of these lengthens instead.
LONG_VOWELS = {'ا': 'a', 'و': 'u', 'ی': 'i', 'ي': 'i'}

ARABIC_RE = re.compile(r'[؀-ۿ]')
# The definite article assimilates before these ("ash-shams", not "al-shams").
SUN_LETTERS = set('تثدذرزسشصضطظلن')


# Words whose conventional romanisation is fixed and would otherwise come out
# mangled by the letter rules.
SPECIAL_WORDS = {
    'الله': 'Allah',
    'لله': 'lillah',
    'بالله': "bi'llah",
    'ﷲ': 'Allah',
    'محمد': 'Muhammad',
    'رسول': 'rasul',
}


def _romanise_word(word):
    bare = ''.join(c for c in word if c not in HARAKAT and c != SHADDA and c != TATWEEL)
    if bare in SPECIAL_WORDS:
        return SPECIAL_WORDS[bare]

    out = []
    last_consonant = ''
    index = 0
    length = len(word)

    # "al-": before a sun letter the l assimilates to it and the letter
    # doubles ("ash-shams"); before a moon letter it stays ("al-qamar").
    if word.startswith('ال') and length > 2:
        following = word[2]
        if following in SUN_LETTERS:
            sound = CONSONANTS.get(following, following)
            out.append('a' + sound + '-' + sound)
            last_consonant = sound
            index = 3
            if index < length and word[index] == SHADDA:
                index += 1
        else:
            out.append('al-')
            last_consonant = 'l'
            index = 2

    while index < length:
        char = word[index]
        if char == TATWEEL:
            index += 1
            continue

        if char in HARAKAT:
            out.append(HARAKAT[char])
            index += 1
            continue

        if char == SHADDA:
            # Doubling applies to the consonant, which may already have had a
            # vowel emitted after it, so the consonant is tracked separately.
            if last_consonant:
                vowel = ''
                while out and out[-1] and out[-1] in ('a', 'i', 'u'):
                    vowel = out.pop() + vowel
                out.append(last_consonant)
                if vowel:
                    out.append(vowel)
            index += 1
            continue

        if char in CONSONANTS:
            nxt = word[index + 1] if index + 1 < length else ''
            # A bare consonant followed by a long vowel letter, with no harakat
            # between them, is a long syllable.
            if nxt in LONG_VOWELS and nxt != char and index > 0:
                out.append(CONSONANTS[char] + LONG_VOWELS[nxt])
                last_consonant = CONSONANTS[char]
                index += 2
                continue
            out.append(CONSONANTS[char])
            if CONSONANTS[char]:
                last_consonant = CONSONANTS[char]
            index += 1
            continue

        out.append(char)
        index += 1

    return ''.join(out)


def transliterate(text):
    """Romanise Arabic/Urdu script, leaving other scripts untouched."""
    if not text:
        return ''
    normalised = unicodedata.normalize('NFC', text)
    lines = []
    for line in normalised.splitlines():
        if not line.strip():
            lines.append('')
            continue
        words = []
        for token in line.split():
            words.append(_romanise_word(token) if ARABIC_RE.search(token) else token)
        rendered = ' '.join(w for w in words if w)
        # Tidy artefacts of the mapping.
        rendered = re.sub(r"''+", "'", rendered)
        rendered = re.sub(r'\s{2,}', ' ', rendered).strip()
        lines.append(rendered)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def can_transliterate(text):
    """Only script that actually needs romanising."""
    return bool(ARABIC_RE.search(text or ''))
