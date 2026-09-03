"""
Filling in a qasida's transliteration and translation.

Used when an editor adds a work by hand: both fields are derived from the
lyrics if they are empty, and neither is ever overwritten. The output is a
draft for review - romanisation of unvocalised script loses the short vowels,
and machine translation of devotional verse is rough.
"""

from .models import Qasida
from .transliterate import can_transliterate, transliterate
from .translating import (
    available_source_codes,
    code_for_language,
    is_native_script,
    translate_verse,
)


def enrich(qasida, overwrite=False):
    """
    Derive the missing fields. Returns a list of the field names changed.

    Nothing an editor typed is replaced unless `overwrite` is set.
    """
    changed = []

    if (overwrite or not qasida.transliteration) and can_transliterate(qasida.lyrics):
        draft = transliterate(qasida.lyrics)
        if draft:
            qasida.transliteration = draft
            changed.append('transliteration')

    wants_translation = overwrite or not qasida.translation
    # A translation published by the source outranks anything generated.
    if qasida.translation_origin == Qasida.TRANSLATION_SOURCE and not overwrite:
        wants_translation = False

    if wants_translation and is_native_script(qasida.lyrics):
        code = code_for_language(qasida.language)
        if code and code in available_source_codes():
            english = translate_verse(qasida.lyrics, code)
            if english:
                qasida.translation = english
                qasida.translation_origin = Qasida.TRANSLATION_MACHINE
                changed += ['translation', 'translation_origin']

    if changed:
        qasida.save(update_fields=changed + ['search_text'])
    return changed
