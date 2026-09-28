"""
Reading text out of PDFs and scans, and repairing what comes out.

The crawlers, the repair commands and the editors' OCR bench all use these,
so they live apart from the scrapers: nothing here fetches anything or knows
where a document came from. It is given bytes or text and hands back text,
along with the measures used to decide whether one reading beats another.
"""

import io
import re
import statistics
import unicodedata

import pymupdf
import pytesseract
from PIL import Image, ImageFilter, ImageOps

ARABIC_RE = re.compile(r'[؀-ۿ]')

# Pages rasterised or read from one PDF. A 300-page scanned book at OCR
# resolution is gigabytes of pixels and hours of Tesseract inside the nightly
# run; the editor's OCR bench stops at the same number.
MAX_PDF_PAGES = 30

# Boilerplate the embedded document viewer leaves in the HTML.
VIEWER_CHROME = (
    'Loading...', 'Taking too long?', 'Reload document',
    'Open in new tab', 'Related Posts',
)


def arabic_len(text):
    return len(ARABIC_RE.findall(text or ''))


# Arabic words run 3-6 letters, so extraction that shatters glyphs leaves
# mostly one-letter tokens; kashida justification also leaves tatweel runs.
SHATTERED_SINGLE_RATIO = 0.25
SHATTERED_TATWEEL_RATIO = 0.02

# Rasterising for OCR wants more detail than rasterising for display.
OCR_DPI = 400
PAGE_IMAGE_DPI = 200
# psm 4 ("columns of text") beat psm 6 and psm 3 on these layouts, and the
# pages are bilingual so both language models are needed.
# psm 4 and 6 win on different layouts, so both are tried and the better
# result kept. Pages are bilingual, hence both language models.
OCR_CONFIGS = ('--psm 6', '--psm 4')
OCR_LANGS = 'ara+eng'

# OCR must keep most of the source's Arabic to be an improvement: a stricter
# page-segmentation mode can score well on shattering simply by recognising
# less, which would silently throw the poem away.
OCR_MIN_COVERAGE = 0.6
OCR_MAX_NOISE = 0.25
LATIN_RE = re.compile(r'[A-Za-z0-9]')


def text_shatter_score(text):
    """Fraction of Arabic tokens that are a single letter, plus the tatweel ratio."""
    tokens = [t for t in re.split(r'\s+', text or '') if ARABIC_RE.search(t)]
    if not tokens:
        return 1.0, 1.0, 0
    singles = sum(1 for t in tokens if len(t.strip('ـ')) <= 1)
    tatweel = (text.count('ـ') / len(text)) if text else 1.0
    return singles / len(tokens), tatweel, len(tokens)


def looks_shattered(text):
    single_ratio, tatweel_ratio, tokens = text_shatter_score(text)
    if tokens < 20:
        return False  # too little Arabic to judge
    return single_ratio > SHATTERED_SINGLE_RATIO or tatweel_ratio > SHATTERED_TATWEEL_RATIO


def pdf_page_pngs(pdf_bytes, dpi=PAGE_IMAGE_DPI, limit=MAX_PDF_PAGES):
    """Rasterise the pages, one at a time. These images are the trustworthy
    record of a document whose text layer cannot be read."""
    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    try:
        for index in range(min(doc.page_count, limit)):
            yield index, doc[index].get_pixmap(dpi=dpi).tobytes('png')
    finally:
        doc.close()


def _ocr_noise_ratio(text):
    """Share of tokens that are Latin/digit debris rather than words."""
    tokens = [t for t in re.split(r'\s+', text or '') if t]
    if not tokens:
        return 1.0
    noise = sum(1 for t in tokens if LATIN_RE.search(t) and not ARABIC_RE.search(t))
    return noise / len(tokens)


def ocr_pdf(pdf_bytes):
    """
    Best-effort transcription of a PDF whose text layer is unusable.

    Each candidate page-segmentation mode is scored and the one recovering the
    most Arabic without shattering is returned.
    """
    best, best_key = '', None
    for config in OCR_CONFIGS:
        chunks = []
        # Rasterised again for each mode rather than held: a few dozen pages at
        # OCR resolution is more memory than a worker should keep at once.
        for _, png in pdf_page_pngs(pdf_bytes, dpi=OCR_DPI):
            with Image.open(io.BytesIO(png)) as page_image:
                chunks.append(pytesseract.image_to_string(
                    page_image, lang=OCR_LANGS, config=config))
        text = unicodedata.normalize('NFKC', "\n".join(chunks)).strip()
        single_ratio, _, tokens = text_shatter_score(text)
        # Prefer more recovered Arabic, then less shattering.
        key = (tokens, -single_ratio)
        if best_key is None or key > best_key:
            best, best_key = text, key
    return best


def ocr_is_improvement(transcript, original):
    """
    Accept OCR only if it reads better *and* did not lose the content.

    Coverage is measured in Arabic letters rather than words: shattering splits
    one word into several tokens, so a token comparison would flatter OCR modes
    that simply recognise less. Letters survive shattering, so they compare
    like for like.
    """
    if not transcript:
        return False
    original_single, _, _ = text_shatter_score(original)
    single, _, tokens = text_shatter_score(transcript)
    if tokens < 20:
        return False
    original_letters = arabic_len(original)
    if original_letters and arabic_len(transcript) < OCR_MIN_COVERAGE * original_letters:
        return False
    if _ocr_noise_ratio(transcript) > OCR_MAX_NOISE:
        return False
    return single < original_single


# --- reading text off a scanned page ----------------------------------------
#
# Sources that publish only photographs of a page leave nothing to extract. The
# scans are around 1000px, which is thin for OCR, so they are upscaled and
# sharpened first: measured against the stored text that lifted the recovered
# Arabic by roughly half again. Tesseract still emits Latin/digit debris around
# the Arabic, so lines carrying no Arabic are dropped afterwards.

SCAN_OCR_UPSCALE = 2
SCAN_OCR_SHARPEN = ImageFilter.UnsharpMask(radius=2, percent=140)
SCAN_OCR_CONFIG = '--psm 4'
# A line needs this share of Arabic characters to count as verse rather than debris.
SCAN_LINE_ARABIC_SHARE = 0.35
# OCR has to beat the stored text by this much before it replaces it.
SCAN_OCR_MIN_GAIN = 1.25


def prepare_scan(image):
    """Upscale and sharpen a scan so Tesseract has more to work with."""
    grey = ImageOps.grayscale(image.convert('RGB'))
    larger = grey.resize(
        (grey.width * SCAN_OCR_UPSCALE, grey.height * SCAN_OCR_UPSCALE), Image.LANCZOS)
    return larger.filter(SCAN_OCR_SHARPEN)


def keep_arabic_lines(text):
    """Strip the Latin/digit debris Tesseract leaves around Arabic verse."""
    kept = []
    for line in (text or '').splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append('')
            continue
        arabic = len(ARABIC_RE.findall(stripped))
        if arabic < 2 or arabic / len(stripped) < SCAN_LINE_ARABIC_SHARE:
            continue
        words = [w for w in stripped.split() if ARABIC_RE.search(w)]
        if words:
            kept.append(' '.join(words))
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()


def ocr_scanned_images(qasida):
    """
    Read the stored scans for one qasida and return cleaned Arabic text.

    Pages are concatenated in their stored order and separated by a blank line,
    so page breaks read as stanza breaks rather than running together.
    """
    pages = []
    for scan in qasida.images.all():
        try:
            with Image.open(scan.image.path) as image:
                raw = pytesseract.image_to_string(
                    prepare_scan(image), lang=OCR_LANGS, config=SCAN_OCR_CONFIG)
        except Exception as e:
            print(f"    scan OCR failed ({type(e).__name__}): {scan.image.name}")
            continue
        cleaned = keep_arabic_lines(unicodedata.normalize('NFKC', raw))
        if cleaned:
            pages.append(cleaned)
    return "\n\n".join(pages).strip()


# --- rebuilding a shattered text layer ------------------------------------
#
# Some source PDFs place every glyph separately and pad with kashida to justify
# the line. Extracting in stream order then yields the right letters in the
# wrong order. The glyph coordinates are still there, so the line can be
# rebuilt by sorting each row right-to-left, which is both lossless and far
# more accurate than OCR on these layouts.

TATWEEL = 'ـ'
DIACRITIC_RE = re.compile(r'[ً-ْٰۖ-ۭ]')

# Fraction of the median glyph width that counts as a word break. Tuned against
# documents whose normal extraction is known to be correct.
WORD_GAP_FACTOR = 0.3

# Arabic has almost no single-letter words, so a lone letter is nearly always a
# fragment of its neighbour. These are the real ones.
REAL_SINGLE_LETTERS = {'و', 'أ', 'ا'}

# Reassembly must not lose letters; below this share of the original it failed.
REASSEMBLY_MIN_COVERAGE = 0.9


# Arabic letters proper: the block from hamza to ya, minus the kashida stretch
# (which sits inside that range). Diacritics fall above it and are excluded.
ARABIC_LETTER_RE = re.compile(r'[ء-ي]')


def _letters_only(text):
    """
    Just the Arabic letters.

    Whitespace has to be excluded, not merely diacritics: shattered text is
    largely spaces, so counting them would flatter it and make any rebuild look
    like it had lost content.
    """
    return ''.join(c for c in ARABIC_LETTER_RE.findall(text or '') if c != TATWEEL)


def _merge_orphan_letters(line):
    """Join stray single letters onto their neighbour."""
    tokens = [t for t in line.split(' ') if t]
    merged = []
    for token in tokens:
        bare = DIACRITIC_RE.sub('', token)
        previous_bare = DIACRITIC_RE.sub('', merged[-1]) if merged else ''
        orphan = (len(bare) == 1 and bare not in REAL_SINGLE_LETTERS
                  and ARABIC_RE.search(bare))
        previous_orphan = (len(previous_bare) == 1
                           and previous_bare not in REAL_SINGLE_LETTERS
                           and ARABIC_RE.search(previous_bare))
        if merged and (orphan or previous_orphan):
            merged[-1] += token
        else:
            merged.append(token)
    return ' '.join(merged)


def _reassemble_page(page):
    rows = {}
    for block in page.get_text("rawdict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    glyph = char["c"]
                    if not glyph.strip():
                        continue  # justification spaces sit anywhere, ignore them
                    x0, y0, x1, y1 = char["bbox"]
                    rows.setdefault(round((y0 + y1) / 2), []).append((x0, x1, glyph))

    lines = []
    for baseline in sorted(rows):
        row = sorted(rows[baseline], key=lambda item: -item[0])  # right to left
        widths = [x1 - x0 for x0, x1, glyph in row
                  if x1 > x0 and glyph != TATWEEL and not DIACRITIC_RE.match(glyph)]
        if not widths:
            continue
        threshold = statistics.median(widths) * WORD_GAP_FACTOR

        pieces, previous_x0 = [], None
        for x0, x1, glyph in row:
            # Kashida bridges the space it occupies, so gaps are measured with
            # it in place and it is only left out of the emitted text.
            if previous_x0 is not None and (previous_x0 - x1) > threshold:
                pieces.append(' ')
            if glyph != TATWEEL:
                pieces.append(glyph)
            previous_x0 = x0

        line = _merge_orphan_letters(re.sub(r' {2,}', ' ', ''.join(pieces)).strip())
        if line:
            lines.append(line)
    return "\n".join(lines)


def reassemble_pdf_text(pdf_bytes):
    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    try:
        text = "\n".join(_reassemble_page(page) for page in doc)
    finally:
        doc.close()
    return unicodedata.normalize('NFKC', text).strip()


def reassembly_is_improvement(rebuilt, original):
    """Accept the rebuild only if it reads better and kept the letters."""
    if not rebuilt or looks_shattered(rebuilt):
        return False
    original_letters = len(_letters_only(original))
    if original_letters and len(_letters_only(rebuilt)) < REASSEMBLY_MIN_COVERAGE * original_letters:
        return False
    return True


def pdf_text(pdf_bytes):
    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    # These PDFs use subset fonts that emit Arabic presentation forms - NFKC
    # folds those back to normal letters.
    return unicodedata.normalize('NFKC', text).strip()


# --- repairing run-together lines -------------------------------------------
#
# Some extractions collapse a whole page onto one line - the worst held 34,000
# characters. The words are all present, only the breaks are missing, and where
# the original and a translation alternate the writing system changes at each
# boundary. Splitting there restores readable lines without altering a
# character of the text.

# Above this a line is not verse, it is a collapsed page.
MEGA_LINE_CHARS = 400
ARABIC_CHAR_RE = re.compile(r'[؀-ۿ]')


def split_at_script_change(line):
    """Break a line where the writing system changes."""
    pieces, current, current_is_arabic = [], [], None
    for char in line:
        if char.isspace():
            current.append(char)
            continue
        is_arabic = bool(ARABIC_CHAR_RE.match(char))
        if current_is_arabic is None:
            current_is_arabic = is_arabic
        elif is_arabic != current_is_arabic:
            piece = ''.join(current).strip()
            if piece:
                pieces.append(piece)
            current, current_is_arabic = [], is_arabic
        current.append(char)
    piece = ''.join(current).strip()
    if piece:
        pieces.append(piece)
    return pieces


def reflow_run_together(text, limit=MEGA_LINE_CHARS):
    """
    Add line breaks to any line long enough to be a collapsed page.

    Only whitespace is introduced: the guard in the command compares the
    characters before and after so nothing can be dropped.
    """
    out = []
    for line in (text or '').splitlines():
        stripped = line.strip()
        if len(stripped) <= limit:
            out.append(stripped)
            continue
        out.extend(split_at_script_change(stripped) or [stripped])
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip()


# --- stripping page furniture ----------------------------------------------
#
# Text lifted from a typeset edition carries the apparatus of the page as well
# as the poem: page numbers, verse numbers, footnote markers, rule characters.
# Interleaved with the verse it makes a page unreadable, so those lines are
# removed. Removal is only accepted when the Arabic survives, which keeps the
# rule from eating a poem that happens to be numbered.

# A line consisting only of digits, punctuation or symbols.
FURNITURE_LINE_RE = re.compile(r'^[\W\d_]+$')
LATIN_WORD_RE = re.compile(r'[A-Za-z]{3}')
# Below this share of furniture a text is left alone.
FURNITURE_THRESHOLD = 0.20
# Cleaning must keep this share of the Arabic letters.
FURNITURE_MIN_KEPT = 0.9


def _is_furniture(line):
    stripped = line.strip()
    if not stripped:
        return False
    if FURNITURE_LINE_RE.match(stripped):
        return True
    # A lone letter or two is a catchword or marker, not a verse.
    if ARABIC_RE.search(stripped) and len(stripped) <= 2:
        return True
    # Neither Arabic nor a real Latin word: symbols and stray marks.
    return not ARABIC_RE.search(stripped) and not LATIN_WORD_RE.search(stripped)


def furniture_ratio(text):
    """Share of non-blank lines that are page apparatus rather than verse."""
    lines = [l for l in (text or '').splitlines() if l.strip()]
    if len(lines) < 20:
        return 0.0
    return sum(1 for l in lines if _is_furniture(l)) / len(lines)


def strip_page_furniture(text):
    """Drop apparatus lines and collapse the gaps they leave."""
    kept = []
    for line in (text or '').splitlines():
        if not line.strip():
            kept.append('')
        elif not _is_furniture(line):
            kept.append(line.strip())
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()


def strip_viewer_chrome(text):
    lines = [ln for ln in text.splitlines() if ln.strip() not in VIEWER_CHROME]
    return "\n".join(lines).strip()
