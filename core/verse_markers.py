"""
Cleaning the asterisks out of crawled verse.

Several sources publish their poetry as Markdown, and the crawler stored what
it was given. Two different things arrive looking the same:

  * emphasis - a title or refrain wrapped in asterisks, `**like this**`, which
    a Markdown renderer would have turned into bold and which we show as raw
    punctuation in the middle of the poem;
  * separators - a run of asterisks with space around it, used to divide the
    two halves of a verse, or one verse from the next.

The second is the more damaging. A source that separates verses with
asterisks rather than line breaks leaves the whole poem on one physical line,
so it has no stanza structure at all: nothing can be paired against it, and
the page falls back to showing every layer as one undivided block. Turning
those separators into real line breaks is what gives the text its shape back,
and a transliteration written one line per half-verse then lines up with it.
"""

import re

# Asterisks hugging the text they wrap, with no space inside: Markdown
# emphasis. Non-greedy, so `**a** b **c**` is two spans and not one.
EMPHASIS_RE = re.compile(r'\*{1,3}(?=\S)(.+?)(?<=\S)\*{1,3}')

# A run of asterisks standing alone between words: a divider.
SEPARATOR_RE = re.compile(r'[ \t ]*\*+[ \t ]*')

# Three or more blank lines say nothing that one does.
EXTRA_BLANKS_RE = re.compile(r'\n{3,}')

TRAILING_SPACE_RE = re.compile(r'[ \t ]+$', re.M)


def _strip_emphasis(line):
    """Unwrap emphasis, keeping a wrapped opening phrase on its own line.

    A source that emphasises the first phrase is nearly always marking a
    heading or a refrain, so it earns a line rather than being run together
    with the verse that follows it.
    """
    match = EMPHASIS_RE.match(line.strip())
    if match and match.end() < len(line.strip()):
        head = match.group(1).strip()
        rest = EMPHASIS_RE.sub(r'\1', line.strip()[match.end():]).strip()
        return f'{head}\n{rest}' if rest else head
    return EMPHASIS_RE.sub(r'\1', line)


def normalise(text):
    """
    Verse with its Markdown removed and its dividers turned into line breaks.

    Emphasis is unwrapped first, so the asterisks that were doing that job are
    gone before whatever is left is read as a divider.
    """
    if not text or '*' not in text:
        return text or ''

    lines = []
    for line in text.splitlines():
        line = _strip_emphasis(line)
        # Whatever asterisks survive were never wrapping anything.
        line = SEPARATOR_RE.sub('\n', line)
        lines.append(line)

    cleaned = '\n'.join(lines)
    cleaned = TRAILING_SPACE_RE.sub('', cleaned)
    cleaned = EXTRA_BLANKS_RE.sub('\n\n', cleaned)
    # A divider at the start or end of a line leaves an empty one behind.
    return '\n'.join(part for part in cleaned.split('\n')
                     if part.strip() or True).strip()


def has_markers(text):
    """Whether this text still carries asterisks that should not be shown."""
    return bool(text) and '*' in text


def describe(text):
    """
    A redacted shape of the text, for reporting what a change would do.

    Scripts are replaced with a placeholder so a dry run can be read - and
    pasted into a ticket - without reproducing the poetry itself.
    """
    if not text:
        return '(empty)'
    shape = re.sub(r'[^\s*|=-]+', '·', text)
    shape = re.sub(r'·+', '·', shape)
    return shape[:160]
