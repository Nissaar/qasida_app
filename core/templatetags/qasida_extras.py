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


def _lines(text):
    """The non-blank lines of a block, which is what a verse actually is."""
    return [line for line in text.splitlines() if line.strip()]


def _regrouped_like(original_blocks, layer_text):
    """
    Reshape a layer into the original's stanza pattern, or None.

    Sources are inconsistent about blank lines: the same work can arrive with
    the original as one block and its transliteration broken into verses, or
    the reverse. When the two disagree about stanzas but hold the same number
    of lines, they are still the same poem line for line, so the layer is cut
    into the original's shape and pairs up after all.

    Only exact line-count agreement counts. Anything looser would put verse
    three of the translation against verse four of the original, which is
    worse than not pairing at all.
    """
    lines = _lines(layer_text)
    shape = [len(_lines(block)) for block in original_blocks]
    if not lines or sum(shape) != len(lines):
        return None

    blocks, cut = [], 0
    for count in shape:
        blocks.append('\n'.join(lines[cut:cut + count]))
        cut += count
    return blocks


def _shape(blocks):
    """How many lines each stanza holds."""
    return [len(_lines(block)) for block in blocks]


def _paired_layer(original_blocks, layer_text):
    """
    The layer arranged against `original_blocks`, or None if it cannot be.

    Tried in order of confidence: the source's own stanza marks first, then
    line-for-line, then give up and let the page show the layer whole.

    Matching stanza counts is not on its own enough, and assuming it was put
    the wrong verses together. A twelve-line original written as stanzas of
    five, four and three, against a transliteration typed as one block, both
    come out as three stanzas - because a long unbroken block is sub-grouped
    into fours for reading rhythm. Three equals three, so they paired, and the
    fifth line of the original sat against nothing while its transliteration
    sat against the next stanza. The shapes have to agree too; where they do
    not, cutting the layer to the original's own shape is what gets it right.
    """
    if not layer_text or not layer_text.strip():
        return None
    blocks = _display_stanzas(layer_text)
    if blocks and _shape(blocks) == _shape(original_blocks):
        return blocks
    return _regrouped_like(original_blocks, layer_text)


def _layers(qasida):
    """The original's stanzas, and each other layer paired against them."""
    original = _display_stanzas(qasida.lyrics)
    return {
        'original': original,
        'latin': _paired_layer(original, qasida.transliteration),
        'translation': _paired_layer(original, qasida.translation),
    }


@register.filter
def stanza_rows(qasida):
    """
    Group the verses into stanzas, each with its Latin and translated form.

    Every layer is paired independently, and only where it can be paired
    honestly. A layer that cannot be is left out of these rows entirely - the
    page then shows it whole, as its own passage, rather than dropping it.
    """
    layers = _layers(qasida)
    original = layers['original']
    latin, translated = layers['latin'], layers['translation']

    rows = []
    for index, block in enumerate(original):
        rows.append({
            'original': block,
            'latin': latin[index] if latin else '',
            'translation': translated[index] if translated else '',
        })
    return rows


@register.filter
def transliteration_is_aligned(qasida):
    """True when the transliteration was shown stanza by stanza above."""
    return _layers(qasida)['latin'] is not None


@register.filter
def translation_is_aligned(qasida):
    """True when the translation was shown stanza by stanza above."""
    return _layers(qasida)['translation'] is not None


@register.simple_tag(takes_context=True)
def is_favourited(context, qasida):
    """
    Whether the signed-in reader has saved this work.

    A card grid asks this two dozen times on one page, so the reader's whole
    set of saved ids is fetched once and kept on the request. Favourite lists
    are personal and small; one query beats one per card by a wide margin.
    """
    request = context.get('request')
    user = getattr(request, 'user', None)
    if request is None or user is None or not user.is_authenticated:
        return False

    ids = getattr(request, '_favourite_ids', None)
    if ids is None:
        from core.models import Favourite
        ids = set(Favourite.objects.filter(user=user).values_list('qasida_id', flat=True))
        request._favourite_ids = ids
    return qasida.pk in ids


@register.simple_tag(takes_context=True)
def reader_profile(context):
    """
    The signed-in reader's layout preferences, or None.

    Cached on the request: the qasida page consults it in two places.
    """
    request = context.get('request')
    user = getattr(request, 'user', None)
    if request is None or user is None or not user.is_authenticated:
        return None

    if not hasattr(request, '_reader_profile'):
        from core.models import ReaderProfile
        request._reader_profile = ReaderProfile.for_user(user)
    return request._reader_profile
