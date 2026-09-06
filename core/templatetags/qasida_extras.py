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


# A bayt is classically one verse of two hemistichs, and Arabic is very often
# typed that way - both halves on one line, separated by a run of spaces -
# while the transliteration and the translation give each half its own line.
# The two then stand in a strict 1:2 ratio and can never match line for line.
HEMISTICH_GAP_RE = re.compile(r'\S[ \t\u00a0]{2,}\S')


def _hemistichs_per_line(original_blocks):
    """
    Two when the original sets both halves of each verse on one line.

    Read from the text rather than assumed: a clear majority of lines must
    carry the wide internal gap that separates the halves. Without that
    evidence this stays at one, and a layer with twice as many lines is left
    unpaired rather than folded together on the strength of the arithmetic
    alone - two happens to be a very easy ratio to hit by coincidence.
    """
    lines = [line for block in original_blocks for line in _lines(block)]
    if not lines:
        return 1
    gapped = sum(1 for line in lines if HEMISTICH_GAP_RE.search(line))
    return 2 if gapped * 3 >= len(lines) * 2 else 1


def _regrouped_like(original_blocks, layer_text, per_line=1):
    """
    Cut a layer into the original's stanza pattern, or None.

    Sources are inconsistent about blank lines: the same work can arrive with
    the original as one block and its transliteration broken into verses, or
    the reverse. When the two disagree about stanzas but hold the same number
    of lines, they are still the same poem line for line, so the layer is cut
    to the original's shape and lines up after all.

    `per_line` is how many of the layer's lines answer to one line of the
    original - two where the original puts both hemistichs of a verse on one
    line and the layer gives each its own.

    Only exact agreement counts. Anything looser would set verse three against
    verse four, which is worse than not pairing at all.
    """
    lines = _lines(layer_text)
    shape = [count * per_line for count in _shape(original_blocks)]
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

    paired = _regrouped_like(original_blocks, layer_text)
    if paired is not None:
        return paired

    # The original may be setting two hemistichs to a line where the layer
    # gives each its own; both halves then sit under the verse they belong to.
    per_line = _hemistichs_per_line(original_blocks)
    if per_line > 1:
        return _regrouped_like(original_blocks, layer_text, per_line)
    return None


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
    """
    Each layer set against `original_blocks` by `matcher`, independently.

    Independently is the whole point. Pairing them all or not at all means one
    layer that cannot be matched drags down every layer that can: a work whose
    transliteration answers the original line for line, but whose translation
    is a single line short, showed all three as undivided blocks because of
    that one line. A layer that will not align is left out and shown whole;
    the ones that align are still read against the verses.
    """
    return {key: matcher(original_blocks, text) for key, text in layers.items()}


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
        # Use this granularity as soon as anything at all lines up at it; a
        # work with no layers to place simply reads at the finest one.
        if not layers or any(blocks is not None for blocks in aligned.values()):
            return [{
                'original': block,
                'latin': (aligned.get('latin') or [''] * len(original))[index],
                'translation': (aligned.get('translation') or [''] * len(original))[index],
            } for index, block in enumerate(original)]

    # Not one layer corresponds. Still the same layers in the same order.
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


LAYER_LABELS = {'latin': 'Latin script', 'translation': 'Translation'}


def _alignment(qasida):
    """Which layers were set against the verses, and which could not be."""
    layers = _present_layers(qasida)
    if not layers:
        return set(), set()

    lyrics = qasida.lyrics or ''
    for original, matcher in ((_display_stanzas(lyrics), _aligned_by_shape),
                              (_stanzas(lyrics), _aligned_by_count)):
        if not original:
            continue
        aligned = _interleave(original, layers, matcher)
        if any(blocks is not None for blocks in aligned.values()):
            return ({key for key, blocks in aligned.items() if blocks is not None},
                    {key for key, blocks in aligned.items() if blocks is None})
    return set(), set(layers)


@register.filter
def layers_are_paired(qasida):
    """True when every layer this work has was set verse by verse."""
    _paired, loose = _alignment(qasida)
    return not loose


@register.filter
def unpaired_layers(qasida):
    """
    The layers that had to be shown whole, with the text to show.

    Named, so the page can say which one does not correspond instead of
    implying that none of them do.
    """
    _paired, loose = _alignment(qasida)
    return [{
        'key': key,
        'label': LAYER_LABELS[key],
        'text': (qasida.transliteration if key == 'latin' else qasida.translation),
    } for key in ('latin', 'translation') if key in loose]


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
