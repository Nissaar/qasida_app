"""
Filling in a qasida's transliteration and translation.

Used when an editor adds a work by hand: both fields are derived from the
lyrics if they are empty, and neither is ever overwritten. The output is a
draft for review - romanisation of unvocalised script loses the short vowels,
and machine translation of devotional verse is rough.
"""

from django.db import transaction

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

    Translating a poem takes seconds to minutes, and the row can change in
    that time. So the drafts are worked out from the copy passed in, and then
    written to a freshly locked copy of the row, re-checking that each field
    is still one to fill. Saving the old copy instead overwrote a translation
    an editor typed in the meantime, and rebuilt the search text from lyrics
    that had since been corrected.
    """
    drafts = {}

    if (overwrite or not qasida.transliteration) and can_transliterate(qasida.lyrics):
        draft = transliterate(qasida.lyrics)
        if draft:
            drafts['transliteration'] = draft

    if _wants_translation(qasida, overwrite) and is_native_script(qasida.lyrics):
        code = code_for_language(qasida.language)
        if code and code in available_source_codes():
            english = translate_verse(qasida.lyrics, code)
            if english:
                drafts['translation'] = english

    if not drafts:
        return []

    with transaction.atomic():
        fresh = Qasida.objects.select_for_update().filter(pk=qasida.pk).first()
        # Gone, or its text changed while we worked: these drafts are of a
        # text that no longer exists. The save that changed it queues its
        # own enrichment.
        if fresh is None or fresh.lyrics != qasida.lyrics:
            return []

        changed = []
        if 'transliteration' in drafts and (overwrite or not fresh.transliteration):
            fresh.transliteration = drafts['transliteration']
            changed.append('transliteration')
        if 'translation' in drafts and _wants_translation(fresh, overwrite):
            fresh.translation = drafts['translation']
            fresh.translation_origin = Qasida.TRANSLATION_MACHINE
            changed += ['translation', 'translation_origin']
        if changed:
            # save() rebuilds search_text and the duplicate signature from
            # this fresh copy, and adds them to update_fields.
            fresh.save(update_fields=changed)
    return changed


def _wants_translation(qasida, overwrite):
    """Whether this row's translation is one to fill in."""
    # A translation published by the source outranks anything generated.
    if qasida.translation_origin == Qasida.TRANSLATION_SOURCE and not overwrite:
        return False
    return overwrite or not qasida.translation
