"""Template helpers for laying out verse text."""

import re

from django import template

register = template.Library()

# One or more blank lines separate stanzas in the stored text.
STANZA_SPLIT_RE = re.compile(r'\n\s*\n+')


# Where a source published no blank lines, long runs are grouped purely for
# reading rhythm. This is typographic help, not a claim about the poem's own
# stanza structure, so it only applies when the source told us nothing.
DISPLAY_GROUP_SIZE = 4
DISPLAY_GROUP_MIN_LINES = 8


def _stanzas(text):
    if not text:
        return []
    blocks = [b.strip('\n') for b in STANZA_SPLIT_RE.split(text.strip())]
    return [b for b in blocks if b.strip()]


def _display_stanzas(text):
    """
    Stanzas to lay out.

    A block the source marked out is kept as it is, unless it is long enough to
    read as a wall - a single stray blank line should not excuse two blocks of
    150 lines - in which case it is sub-grouped for rhythm.
    """
    laid_out = []
    for block in _stanzas(text):
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) < DISPLAY_GROUP_MIN_LINES:
            laid_out.append(block)
            continue
        laid_out.extend(
            '\n'.join(lines[index:index + DISPLAY_GROUP_SIZE])
            for index in range(0, len(lines), DISPLAY_GROUP_SIZE)
        )
    return laid_out


@register.filter
def stanzas(text):
    """Split verse text into stanza blocks for display."""
    return _display_stanzas(text)


@register.filter
def stanza_rows(qasida):
    """
    Group the verses into stanzas, each with its Latin and translated form.

    Every layer is aligned independently: a layer is only shown against a
    stanza when its own stanza count matches the original, so a translation
    that is laid out differently is left for the page to show as one passage
    instead of being paired against the wrong verse.
    """
    original = _display_stanzas(qasida.lyrics)
    latin = _display_stanzas(qasida.transliteration)
    translated = _display_stanzas(qasida.translation)

    latin_aligned = bool(latin) and len(latin) == len(original)
    translation_aligned = bool(translated) and len(translated) == len(original)

    rows = []
    for index, block in enumerate(original):
        rows.append({
            'original': block,
            'latin': latin[index] if latin_aligned else '',
            'translation': translated[index] if translation_aligned else '',
        })
    return rows


@register.filter
def translation_is_aligned(qasida):
    """True when the translation was shown stanza by stanza above."""
    original = _display_stanzas(qasida.lyrics)
    translated = _display_stanzas(qasida.translation)
    return bool(translated) and len(translated) == len(original)
