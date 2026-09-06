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


def _shape(blocks):
    """How many lines each stanza holds."""
    return [len(_lines(block)) for block in blocks]


def _regrouped_like(original_blocks, layer_text):
    """
    Cut a layer into the original's stanza pattern, or None.

    Sources are inconsistent about blank lines: the same work can arrive with
    the original as one block and its transliteration broken into verses, or
    the reverse. When the two disagree about stanzas but hold the same number
    of lines, they are still the same poem line for line, so the layer is cut
    to the original's shape and lines up after all.

    Only exact line agreement counts. Anything looser would set verse three
    against verse four, which is worse than not pairing at all.
    """
    lines = _lines(layer_text)
    shape = _shape(original_blocks)
    if not lines or sum(shape) != len(lines):
        return None

    blocks, cut = [], 0
    for count in shape:
        blocks.append('\n'.join(lines[cut:cut + count]))
        cut += count
    return blocks


def _aligned_by_shape(original_blocks, layer_text):
    """
    Line-for-line agreement with `original_blocks`, or None.

    Used against the display stanzas, which are partly our own doing: a long
    unbroken block is sub-grouped into fours for reading rhythm, so its
    "stanzas" are not the source's and matching their count alone means
    nothing. The line counts have to agree too.
    """
    blocks = _stanzas(layer_text)
    if blocks and _shape(blocks) == _shape(original_blocks):
        return blocks
    return _regrouped_like(original_blocks, layer_text)


def _aligned_by_count(original_blocks, layer_text):
    """
    Stanza-for-stanza agreement with `original_blocks`, or None.

    Used against the source's own stanza marks, where equal counts do mean
    something: the fourth stanza of a transliteration belongs against the
    fourth stanza of the original, whether or not the two hold the same
    number of lines. Arabic often sets two hemistichs on one line where the
    transliteration gives each its own, so insisting on equal lines here
    would refuse a pairing that is plainly right.
    """
    blocks = _stanzas(layer_text)
    if blocks and len(blocks) == len(original_blocks):
        return blocks
    return None


def _interleave(original_blocks, layers, matcher):
    """Every layer set against `original_blocks` by `matcher`, or None."""
    aligned = {}
    for key, text in layers.items():
        blocks = matcher(original_blocks, text)
        if blocks is None:
            return None
        aligned[key] = blocks
    return aligned


@register.filter
def stanza_rows(qasida):
    """
    The verses, each followed by its Latin script and its translation.

    Every work is laid out this way; the only question is how finely the
    layers can be set against each other, which is settled at the coarsest
    granularity all of them can honestly support:

      * by display stanza, so a long poem is broken up for reading rhythm -
        this needs the lines to correspond;
      * failing that, by the stanza marks the source itself published;
      * failing that, one block each.

    A layer is never dropped and never set against the wrong verse. Where the
    layers cannot be paired at all, the page still reads original, then Latin,
    then translation - just in whole blocks rather than verse by verse.
    """
    lyrics = qasida.lyrics or ''
    layers = _present_layers(qasida)

    for original, matcher in ((_display_stanzas(lyrics), _aligned_by_shape),
                              (_stanzas(lyrics), _aligned_by_count)):
        if not original:
            continue
        aligned = _interleave(original, layers, matcher)
        if aligned is None:
            continue
        return [{
            'original': block,
            'latin': aligned.get('latin', [''] * len(original))[index],
            'translation': aligned.get('translation', [''] * len(original))[index],
        } for index, block in enumerate(original)]

    # Nothing corresponds. Still the same three layers in the same order.
    return [{
        'original': lyrics.strip(),
        'latin': layers.get('latin', '').strip(),
        'translation': layers.get('translation', '').strip(),
    }]


def _present_layers(qasida):
    return {key: text for key, text in (
        ('latin', qasida.transliteration or ''),
        ('translation', qasida.translation or ''),
    ) if text.strip()}


@register.filter
def layers_are_paired(qasida):
    """
    Whether the layers were set verse by verse rather than as whole blocks.

    The page uses this only to say so, quietly, when they were not, so a
    reader is never left to assume a correspondence that is not there.
    """
    layers = _present_layers(qasida)
    if not layers:
        return True
    lyrics = qasida.lyrics or ''
    for original, matcher in ((_display_stanzas(lyrics), _aligned_by_shape),
                              (_stanzas(lyrics), _aligned_by_count)):
        if original and _interleave(original, layers, matcher) is not None:
            return True
    return False


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
