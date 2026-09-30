"""
Self-hosted translation.

Everything runs in-process from local model files - no external service, no
API key, nothing sent anywhere. Two open-source engines are used:

- OPUS-MT, from the University of Helsinki (CC BY 4.0), for Arabic: its
  large Arabic-to-English model reads classical verse far better than the
  small one Argos ships. It is converted at build time for CTranslate2, the
  inference engine Argos already brings, so it adds a model and no packages.
- Argos Translate (MIT) for everything else - Urdu and Persian - and for
  Arabic too if the OPUS model is not present.

Models are installed into the image at build time so a fresh deployment
translates without needing network access.

Verse is translated line by line rather than as one block. That costs more
calls but keeps the blank-line structure, so a machine translation lines up
stanza for stanza with the original and can be shown beside it.
"""

import logging
import re
import threading
from pathlib import Path

from django.conf import settings

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

# OPUS-MT models, by source language, as directories under
# settings.TRANSLATION_MODELS_DIR. Each holds a CTranslate2 model.bin and the
# SentencePiece source.spm and target.spm it was trained with.
OPUS_MODELS = {'ar': 'opus-mt-tc-big-ar-en'}

ENGINE_NAMES = {'opus': 'OPUS-MT (University of Helsinki)', 'argos': 'Argos Translate'}

_translators = {}
_lock = threading.Lock()

logger = logging.getLogger(__name__)


class TranslationFailed(Exception):
    """Every line the engine was given failed; carries the first reason."""


class _OpusTranslator:
    """One OPUS-MT model, answering .translate(text) the way Argos does."""

    name = 'opus'

    def __init__(self, directory):
        import ctranslate2
        import sentencepiece

        self._model = ctranslate2.Translator(
            str(directory), device='cpu', compute_type='int8',
            inter_threads=1, intra_threads=2)
        self._source = sentencepiece.SentencePieceProcessor(
            model_file=str(directory / 'source.spm'))
        self._target = sentencepiece.SentencePieceProcessor(
            model_file=str(directory / 'target.spm'))

    def translate(self, text):
        # Marian models need the end-of-sentence marker spelled out; without
        # it they never stop, and return a page of unrelated words.
        tokens = self._source.encode(text, out_type=str) + ['</s>']
        result = self._model.translate_batch([tokens], beam_size=4,
                                             max_decoding_length=256)
        return self._target.decode(result[0].hypotheses[0])


class _ArgosTranslator:
    name = 'argos'

    def __init__(self, translation):
        self._translation = translation

    def translate(self, text):
        return self._translation.translate(text)


def _keep_stanza_offline():
    """
    Stop Argos's sentence splitter reaching for the internet.

    Argos splits text with Stanza, and creates its Stanza pipeline with the
    default download setting, which fetches Stanza's resources index from
    GitHub every time a pipeline starts - even though every Argos package
    ships that index alongside its model. On a worker with no route to the
    internet each line then failed with a name-resolution error, and every
    Urdu translation came back empty. Reusing the index already on disk needs
    no network; Stanza only downloads if the file were missing.
    """
    try:
        import stanza
        from argostranslate import sbd, settings as argos_settings
    except ImportError:
        return
    if getattr(sbd.StanzaSentencizer, '_offline', False):
        return

    def lazy_pipeline(self):
        if self.stanza_pipeline is None:
            self.stanza_pipeline = stanza.Pipeline(
                lang=self.stanza_lang_code,
                dir=str(self.pkg.package_path / 'stanza'),
                processors='tokenize',
                use_gpu=argos_settings.device == 'cuda',
                logging_level='WARNING',
                download_method=stanza.DownloadMethod.REUSE_RESOURCES,
            )
        return self.stanza_pipeline

    sbd.StanzaSentencizer.lazy_pipeline = lazy_pipeline
    sbd.StanzaSentencizer._offline = True


def _opus_directory(source_code):
    name = OPUS_MODELS.get(source_code)
    base = getattr(settings, 'TRANSLATION_MODELS_DIR', '')
    if not name or not base:
        return None
    directory = Path(base) / name
    return directory if (directory / 'model.bin').exists() else None


def is_native_script(text):
    """True when the text is really in its own script, not transliterated."""
    native = len(NATIVE_SCRIPT_RE.findall(text or ''))
    latin = len(LATIN_RE.findall(text or ''))
    if native < MIN_NATIVE_CHARS:
        return False
    return native / (native + latin) >= MIN_NATIVE_SHARE


def available_source_codes():
    """Languages this container can translate from, by either engine."""
    codes = {code for code in OPUS_MODELS if _opus_directory(code)}
    try:
        from argostranslate import translate
        installed = {language.code for language in translate.get_installed_languages()}
    except ImportError:
        installed = set()
    if TARGET_CODE in installed:
        codes |= {code for code in LANGUAGE_CODES.values() if code in installed}
    return codes


def engine_for(source_code):
    """The name of the engine that would translate this language, or None."""
    engine = _translator(source_code)
    return ENGINE_NAMES[engine.name] if engine is not None else None


def code_for_language(name):
    return LANGUAGE_CODES.get((name or '').strip().lower())


def _translator(source_code):
    """
    One translator per language, loaded once; loading a model is expensive.

    OPUS-MT where there is a model for the language, else Argos.
    """
    with _lock:
        if source_code in _translators:
            return _translators[source_code]
        engine = None
        directory = _opus_directory(source_code)
        if directory is not None:
            try:
                engine = _OpusTranslator(directory)
            except Exception:
                engine = None  # a damaged model: fall back to Argos
        if engine is None:
            _keep_stanza_offline()
            try:
                from argostranslate import translate
                languages = {language.code: language
                             for language in translate.get_installed_languages()}
            except ImportError:
                languages = {}
            source, target = languages.get(source_code), languages.get(TARGET_CODE)
            if source is not None and target is not None:
                engine = _ArgosTranslator(source.get_translation(target))
        _translators[source_code] = engine
        return engine


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


def translate_verse(text, source_code, strict=False):
    """
    Translate verse, preserving its line and stanza structure.

    Returns '' when no model is installed for the language, so callers can
    tell "not translated" from "translated to nothing". A line the engine
    fails on is left blank so one bad line does not lose the poem; with
    `strict`, a text on which every line failed raises TranslationFailed
    with the engine's own error, rather than quietly returning nothing.
    """
    engine = _translator(source_code)
    if engine is None or not text:
        return ''
    if not is_native_script(text):
        # Latin-script transliteration: the model would produce nonsense.
        return ''

    errors = []
    output = []
    for stanza in re.split(r'\n\s*\n', text.strip()):
        lines = [line.strip() for line in stanza.splitlines() if line.strip()]
        if engine.name == 'opus' and len(lines) == 2:
            # A couplet is one verse: in classical Arabic its two halves are
            # one sentence, and translated apart each loses its sense ("He's
            # the lover you're asking for. / Every hall of terror is broken
            # into." against "He is the beloved whose intercession is hoped
            # for..."). The larger model reads the whole bayt; the smaller
            # Argos models do worse on the longer input, so they keep lines.
            output.append(_translate_line(engine, ' '.join(lines), errors,
                                          limit=2 * MAX_LINE_CHARS))
        else:
            output.extend(_translate_line(engine, line, errors) for line in lines)
        output.append('')
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(output)).strip()

    if errors:
        # Once per text, with the traceback: a failure that is the same on
        # every line is one fault, not forty.
        first = errors[0]
        logger.error('Translating %s with %s: %d line(s) failed; the first: %s: %s',
                     source_code, engine.name, len(errors), type(first).__name__, first,
                     exc_info=(type(first), first, first.__traceback__))
        if strict and not re.search(r'[A-Za-z]', result):
            raise TranslationFailed(f'{type(first).__name__}: {first}') from first
    return result


def _translate_line(engine, line, errors, limit=MAX_LINE_CHARS):
    if len(line) < MIN_LINE_CHARS:
        return line
    try:
        rendered = [engine.translate(piece).strip() for piece in _split_long_line(line, limit)]
        return ' '.join(p for p in rendered if p)
    except Exception as error:
        # One bad line should not lose the rest of the poem.
        errors.append(error)
        return ''
