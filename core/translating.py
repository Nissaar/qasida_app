"""
Self-hosted translation.

Uses Argos Translate, which runs entirely in-process from local model files -
no external service and no API key. Models are installed into the image at
build time so a fresh deployment translates without needing network access.

Verse is translated line by line rather than as one block. That costs more
calls but keeps the blank-line structure, so a machine translation lines up
stanza for stanza with the original and can be shown beside it.
"""

import re
import threading

# Languages we hold text in, mapped to Argos codes. Punjabi has no Argos
# package, so those rows are left untranslated rather than mislabelled.
LANGUAGE_CODES = {
    'arabic': 'ar',
    'urdu': 'ur',
    'persian': 'fa',
    'farsi': 'fa',
}

TARGET_CODE = 'en'

# Lines shorter than this are markers or refrain fragments; translating them
# in isolation produces noise.
MIN_LINE_CHARS = 4

# The model silently truncates long inputs - a 1234-character line came back as
# 9 characters - so anything longer is split at word boundaries and translated
# in pieces. Verse often carries no sentence punctuation, so length is the only
# reliable place to divide.
MAX_LINE_CHARS = 180

# Script detection. The language field records the language of the *work*, but
# some sources publish it in Latin transliteration rather than its own script.
# Running an Arabic or Urdu model over Latin text yields nonsense, so the script
# actually present decides whether a row can be translated.
NATIVE_SCRIPT_RE = re.compile(r'[؀-ۿ]')
LATIN_RE = re.compile(r'[A-Za-z]')
# The text must be predominantly native script before a model is applied.
MIN_NATIVE_CHARS = 40
MIN_NATIVE_SHARE = 0.5

_translators = {}
_lock = threading.Lock()


def is_native_script(text):
    """True when the text is really in its own script, not transliterated."""
    native = len(NATIVE_SCRIPT_RE.findall(text or ''))
    latin = len(LATIN_RE.findall(text or ''))
    if native < MIN_NATIVE_CHARS:
        return False
    return native / (native + latin) >= MIN_NATIVE_SHARE


def available_source_codes():
    """Argos codes installed in this container that we can translate from."""
    from argostranslate import translate

    installed = {language.code for language in translate.get_installed_languages()}
    if TARGET_CODE not in installed:
        return set()
    return {code for code in LANGUAGE_CODES.values() if code in installed}


def code_for_language(name):
    return LANGUAGE_CODES.get((name or '').strip().lower())


def _translator(source_code):
    """Cache one translator per language pair; loading a model is expensive."""
    with _lock:
        if source_code in _translators:
            return _translators[source_code]
        from argostranslate import translate

        languages = {language.code: language for language in translate.get_installed_languages()}
        source, target = languages.get(source_code), languages.get(TARGET_CODE)
        if source is None or target is None:
            _translators[source_code] = None
        else:
            _translators[source_code] = source.get_translation(target)
        return _translators[source_code]


def _split_long_line(line, limit=MAX_LINE_CHARS):
    """Break an over-long line into word-bounded pieces the model can handle."""
    if len(line) <= limit:
        return [line]
    pieces, current = [], ''
    for word in line.split():
        if current and len(current) + 1 + len(word) > limit:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        pieces.append(current)
    return pieces


def translate_verse(text, source_code):
    """
    Translate verse, preserving its line and stanza structure.

    Returns '' when no model is installed for the language, so callers can
    tell "not translated" from "translated to nothing".
    """
    engine = _translator(source_code)
    if engine is None or not text:
        return ''
    if not is_native_script(text):
        # Latin-script transliteration: the model would produce nonsense.
        return ''

    output = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            output.append('')
            continue
        if len(stripped) < MIN_LINE_CHARS:
            output.append(stripped)
            continue
        try:
            rendered = [engine.translate(piece).strip()
                        for piece in _split_long_line(stripped)]
            output.append(' '.join(p for p in rendered if p))
        except Exception:
            # One bad line should not lose the rest of the poem.
            output.append('')
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(output)).strip()
