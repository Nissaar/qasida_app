"""
Pulling one poem out of a page that publishes it in several scripts at once.

A family of naat sites prints every text three times on one page - in Roman
letters, in Devanagari for Hindi readers, and in the Urdu original - usually
under headings or tabs saying which is which, with the poet and reciter
credited in each. That is the most useful shape a source can have: the
original script and a transliteration of it, already matched line for line.

Rather than trusting each site's markup for where one version ends, every
line is sorted by the alphabet it is written in. Urdu becomes the lyrics,
Roman the transliteration, and Devanagari is set aside, since the library does
not hold Hindi. Stanza breaks survive within each: a paragraph break in the
page is a stanza break in whichever version it falls.
"""

import re

from bs4 import BeautifulSoup, Comment

ARABIC_RE = re.compile(r'[؀-ۿ]')
DEVANAGARI_RE = re.compile(r'[ऀ-ॿ]')
LATIN_RE = re.compile(r'[A-Za-z]')

# Page furniture, removed before the text is read: section headings and tabs,
# links that only jump within the page, share buttons.
FURNITURE = ('script', 'style', 'button', 'svg', 'noscript', 'iframe', 'h1', 'h2', 'h3',
             '.naat-language-nav', '.naat-back-top', '.naat-header',
             '.sws_supernormalaction', '.sharedaddy', '.jp-relatedposts')
BLOCKS = ('p', 'div', 'hr', 'li', 'ul', 'ol', 'blockquote', 'section', 'table', 'tr')

# Credit lines, in each script: "Shayar: <name>" is the poet, "Naat-Khwaan:"
# the reciter. The name is on the same line or the next one.
POET_RE = re.compile(r'^(?:shayar|shaayar|shayer|poet|kalaam|kalam|شاعر|کلام|शायर)\s*[:：]\s*(.*)$', re.I)
RECITER_RE = re.compile(
    r"^(?:na['’`]?a?t[- ]?khwaa?n|reciter|nasheed khwaan|نعت خواں|ना['’`]?त-?ख़?्?वाँ?|ना.त.ख़्वाँ)\s*[:：]\s*(.*)$", re.I)
LEFTOVER_RE = re.compile(r'^(share|roman|roman english|hindi|urdu|हिन्दी|हिंदी|اردو|↑?\s*back to top)$', re.I)


def _lines(html):
    """The page as lines, with '' wherever a paragraph or block ends."""
    soup = BeautifulSoup(html or '', 'html.parser')
    marked_urdu = soup.select_one('#urdu-section, #urdu') is not None
    for selector in FURNITURE:
        for node in soup.select(selector):
            node.decompose()
    for link in soup.select('a[href^="#"]'):
        link.decompose()
    # Newlines in the page source are formatting, not the poem's: only <br>
    # and block boundaries say where a line or a stanza ends.
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()  # "<!--Roman Urdu-->" is a note to the author, not a line
    for text in soup.find_all(string=True):
        text.replace_with(re.sub(r'\s+', ' ', str(text)))
    for line_break in soup.find_all('br'):
        line_break.replace_with('\n')
    for block in soup.find_all(BLOCKS):
        block.insert_before('\n\n')
        block.insert_after('\n\n')
    lines = [' '.join(line.split()) for line in soup.get_text().split('\n')]
    # Where a page marks its Urdu version, it is the last of the poem, and
    # anything after its final line is the site's own - links to other posts,
    # a paragraph about the naat - which read as lines would join the
    # transliteration. The markup cannot be trusted to say where the section
    # ends (one site leaves its sections unclosed), so the last Urdu line does.
    if marked_urdu:
        urdu_at = [n for n, line in enumerate(lines) if _script(line) == 'urdu']
        if urdu_at:
            lines = lines[:urdu_at[-1] + 1]
    return lines


def _script(line):
    counts = {'urdu': len(ARABIC_RE.findall(line)),
              'hindi': len(DEVANAGARI_RE.findall(line)),
              'roman': len(LATIN_RE.findall(line))}
    best = max(counts, key=counts.get)
    return best if counts[best] else None


def _credit(lines, index, pattern):
    """The name a credit line gives, and how many lines it took up."""
    found = pattern.match(lines[index])
    if not found:
        return None, 0
    if found.group(1).strip():
        return found.group(1).strip(), 1
    following = next((n for n in range(index + 1, min(index + 3, len(lines)))
                      if lines[n]), None)
    if following is None:
        return '', 1
    return lines[following], following - index + 1


def split(html):
    """
    {'urdu': text, 'roman': text, 'poet': name, 'poet_native': name}.

    Either text may be '' when the page does not carry that version.
    """
    lines = _lines(html)
    versions = {'urdu': [], 'roman': [], 'hindi': []}
    pending_break = {key: False for key in versions}
    poet, poet_native = '', ''

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line:
            for key in versions:
                pending_break[key] = bool(versions[key])
            index += 1
            continue
        name, used = _credit(lines, index, POET_RE)
        if used:
            if name and ARABIC_RE.search(name):
                poet_native = poet_native or name
            elif name and not DEVANAGARI_RE.search(name):
                poet = poet or name
            index += used
            continue
        _name, used = _credit(lines, index, RECITER_RE)
        if used:
            index += used
            continue
        if LEFTOVER_RE.match(line):
            index += 1
            continue

        script = _script(line)
        if script:
            if pending_break[script]:
                versions[script].append('')
                pending_break[script] = False
            versions[script].append(line)
        index += 1

    text = {key: re.sub(r'\n{3,}', '\n\n', '\n'.join(value)).strip()
            for key, value in versions.items()}
    return {'urdu': text['urdu'], 'roman': text['roman'],
            'poet': poet[:200], 'poet_native': poet_native[:200]}


# The descriptions both sites fold into a title: "X-Naat Lyrics in Roman
# English, Hindi and Urdu || <Devanagari>", "X Lyrics / <Devanagari>".
TITLE_TAIL_RE = re.compile(
    r'\s*[-–—|]*\s*(?:naat|manqabat|manqbat|hamd|salam|salaam|kalam|kalaam|nasheed|qaseeda|qasida)?'
    r'\s*lyrics\b.*$', re.I)


def clean_title(title):
    """The work's name, without the site's description of its page."""
    title = re.split(r'\s*(?:\|\||\s/\s)\s*', title or '', maxsplit=1)[0]
    title = TITLE_TAIL_RE.sub('', title)
    return ' '.join(title.split()).strip(' -–—|')
