"""
Marking where two copies of a poem differ, for an editor reading them side by side.

Ruling on a suspected duplicate means reading one copy against the other, and
most of the time the two agree for stanza after stanza and part on a handful
of words. Only those words are marked, so the eye goes straight to them.

Words are compared the way search compares them - vowel marks, tatweel and
punctuation folded away - because a vocalised copy beside an unvocalised one
is the commonest pair there is, and marking every word of it would mark
nothing. Whitespace and line breaks are kept exactly as each copy has them.
"""

import re
from difflib import SequenceMatcher

from django.utils.html import escape
from django.utils.safestring import mark_safe

from .search import normalize

# A word and the whitespace after it, so line breaks survive the round trip.
TOKEN_RE = re.compile(r'(\S+)(\s*)')

# Beyond this many words a side, the comparison costs more than it is worth
# on a page load; the panels are shown unmarked instead.
MAX_WORDS = 15000

# How many word edits the exact comparison will look for before settling for
# an approximate one. Two copies of one poem differ by a handful; two texts
# this far apart are not a pair anyone will read word by word.
MAX_EDITS = 1500


def _tokens(text):
    leading = re.match(r'\s*', text).group(0)
    return leading, [(word, space) for word, space in TOKEN_RE.findall(text[len(leading):])]


def _render(leading, tokens, changed):
    parts = [escape(leading)]
    run = []

    def flush():
        if run:
            # The space after the last word stays outside the mark, so a
            # highlight never runs on into the next line.
            inner = ''.join(escape(w) + escape(s) for w, s in run[:-1]) + escape(run[-1][0])
            parts.append(f'<mark class="q-diff">{inner}</mark>{escape(run[-1][1])}')
            run.clear()

    for index, (word, space) in enumerate(tokens):
        if index in changed:
            run.append((word, space))
        else:
            flush()
            parts.append(escape(word) + escape(space))
    flush()
    return mark_safe(''.join(parts))


def _matching_pairs(a, b, max_edits=MAX_EDITS):
    """
    Index pairs (i, j) with a[i] == b[j], for the fewest possible edits.

    Myers' algorithm, the one git uses. difflib's matcher takes the longest
    common run first and builds out from it, which goes badly wrong on verse:
    a refrain repeats, the longest run is found a stanza or two out of step,
    and a single changed word marks whole stanzas as different. Returns None
    when the two need more than `max_edits` edits.
    """
    n, m = len(a), len(b)
    offset = n + m + 1
    v = [0] * (2 * offset + 1)
    trace = []
    for d in range(min(n + m, max_edits) + 1):
        trace.append(v[:])
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[offset + k - 1] < v[offset + k + 1]):
                x = v[offset + k + 1]
            else:
                x = v[offset + k - 1] + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[offset + k] = x
            if x >= n and y >= m:
                return _walk_back(trace, n, m, offset)
    return None


def _walk_back(trace, x, y, offset):
    pairs = []
    for d in range(len(trace) - 1, -1, -1):
        v = trace[d]
        k = x - y
        if k == -d or (k != d and v[offset + k - 1] < v[offset + k + 1]):
            previous_k = k + 1
        else:
            previous_k = k - 1
        previous_x = v[offset + previous_k]
        previous_y = previous_x - previous_k
        while x > previous_x and y > previous_y:
            x, y = x - 1, y - 1
            pairs.append((x, y))
        if d == 0:
            break
        x, y = previous_x, previous_y
    pairs.reverse()
    return pairs


def _approximate_pairs(a, b):
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    return [(i + offset, j + offset)
            for i, j, size in matcher.get_matching_blocks() for offset in range(size)]


def highlight_differences(first, second):
    """
    (first_html, second_html, differences), with differing words marked.

    `differences` counts the places the two part, or is None when the texts
    were too long to compare and are returned unmarked.
    """
    lead_a, tokens_a = _tokens(first or '')
    lead_b, tokens_b = _tokens(second or '')
    if len(tokens_a) > MAX_WORDS or len(tokens_b) > MAX_WORDS:
        return (_render(lead_a, tokens_a, set()), _render(lead_b, tokens_b, set()), None)

    keys_a = [normalize(word) or word for word, _ in tokens_a]
    keys_b = [normalize(word) or word for word, _ in tokens_b]
    pairs = _matching_pairs(keys_a, keys_b)
    if pairs is None:
        pairs = _approximate_pairs(keys_a, keys_b)

    changed_a = set(range(len(keys_a))) - {i for i, _ in pairs}
    changed_b = set(range(len(keys_b))) - {j for _, j in pairs}
    # One difference per place the two part, however many words it spans.
    differences, previous = 0, (-1, -1)
    for i, j in pairs + [(len(keys_a), len(keys_b))]:
        if i - previous[0] > 1 or j - previous[1] > 1:
            differences += 1
        previous = (i, j)
    return (_render(lead_a, tokens_a, changed_a), _render(lead_b, tokens_b, changed_b),
            differences)
