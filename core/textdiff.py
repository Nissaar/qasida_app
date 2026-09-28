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
    matcher = SequenceMatcher(None, keys_a, keys_b, autojunk=False)

    changed_a, changed_b, differences = set(), set(), 0
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        differences += 1
        changed_a.update(range(a0, a1))
        changed_b.update(range(b0, b1))
    return (_render(lead_a, tokens_a, changed_a), _render(lead_b, tokens_b, changed_b),
            differences)
