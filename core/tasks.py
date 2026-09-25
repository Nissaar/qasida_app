import hashlib
import html
import io
import json
import re
import statistics
import time
import unicodedata
import uuid
from collections import Counter
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import pymupdf
import pytesseract
import requests
from bs4 import BeautifulSoup
from celery import shared_task
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils.text import slugify
from PIL import Image, ImageFilter, ImageOps

from .extract import _as_text, extract_work
from .fetching import BotChallenge, RateLimited, USER_AGENT, polite_get
from .models import Poet, Qasida, QasidaImage, Tag, SourceWebsite
from .titles import split_title

ARABIC_RE = re.compile(r'[؀-ۿ]')
PDF_URL_RE = re.compile(r'https?://[^\s"\'<>]+?\.pdf')

# Pages that only host a document viewer still carry a few Arabic characters in
# their headings, so a page needs more than that before we call it a poem.
MIN_ARABIC_CHARS = 120

# Below this width or height an image is a flag, spacer or icon, not a scan.
MIN_IMAGE_PX = 300

IMAGE_EXT_RE = re.compile(r'\.(jpe?g|png)$', re.I)
# WordPress links resized copies alongside the original (foo-300x200.jpg).
WP_SIZE_SUFFIX_RE = re.compile(r'-\d+x\d+\.(jpe?g|png)$', re.I)
# Chrome that lives in the same markup as the scans.
IMAGE_DENY_RE = re.compile(
    r'/(?:flags|icons|wp-includes|emoji)/|/wp-content/(?:themes|plugins)/|gravatar\.com', re.I)

# Tag applied to qasidas whose text exists only as a scan, so they stay
# findable through the normal tag facets.
SCAN_ONLY_TAG = 'lyrics-in-images'

# Tag applied when the stored text could not be trusted, so it can be found
# and reviewed rather than quietly served as if it were correct.
UNRELIABLE_TEXT_TAG = 'text-needs-review'

# Tag applied when a source publishes only a romanised text and the original
# script is missing. These are the rows worth pairing with an Arabic or Urdu
# copy from another source, so they need to be findable as a group.
NO_ORIGINAL_TAG = 'needs-original-script'

# Boilerplate the embedded document viewer leaves in the HTML.
VIEWER_CHROME = (
    'Loading...', 'Taking too long?', 'Reload document',
    'Open in new tab', 'Related Posts',
)


# Size limits for the two kinds of file the crawlers download whole.
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_SCAN_BYTES = 15 * 1024 * 1024

# Pages rasterised or read from one PDF. A 300-page scanned book at OCR
# resolution is gigabytes of pixels and hours of Tesseract inside the nightly
# run; the editor's OCR bench stops at the same number.
MAX_PDF_PAGES = 30

# How long a scan that failed to decode, or was too small to be a page, is
# remembered and not fetched again. Icons and error pages do not turn into
# scans, and every nightly pass was downloading them all over again.
REJECTED_SCAN_TTL = 30 * 24 * 60 * 60

# Refusals in a row that park a source for the night. One page that answers
# 500 forever is that page's problem, and used to stop the whole source at the
# same place every night; several pages in a row is the host asking us to stop.
CONSECUTIVE_REFUSALS = 3


class _Refusals:
    """Counts rate-limit refusals in a row, re-raising once there are too many."""

    def __init__(self, limit=CONSECUTIVE_REFUSALS):
        self.limit = limit
        self.in_a_row = 0

    def refused(self, error):
        self.in_a_row += 1
        if self.in_a_row >= self.limit:
            raise error

    def answered(self):
        self.in_a_row = 0


def _safe_source_url(url):
    """
    The URL if it can be stored and linked to as a work's source, else ''.

    These come out of third-party pages and JSON, and are rendered as a link
    on the work's page. Only an ordinary web address that fits the column is
    accepted: a `javascript:` URL would run in a reader's browser, and an
    overlong one used to fail the insert and stop the whole source.
    """
    url = (url or '').strip()
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or len(url) > 500:
        return ''
    return url


def _same_host(url, host):
    """
    Whether `url` is an http(s) address on `host` itself.

    Compared on the parsed hostname. A prefix test on the URL passes
    `https://site.com.evil.tld/` and `https://site.com@10.0.0.5/`, the second
    of which requests sends to 10.0.0.5.
    """
    parts = urlsplit(url or '')
    return (parts.scheme in ('http', 'https')
            and (parts.hostname or '').lower() == (host or '').lower())


def _create_work(website, *, title, source_url, language, tags=(), author='',
                 native_title='', **fields):
    """
    Store one crawled work and tag it.

    Every field is held to the length of its column here, once, so a long
    title from any source is shortened rather than failing the insert.
    """
    qasida = Qasida.objects.create(
        title=(title or 'Untitled')[:200],
        native_title=(native_title or '')[:200],
        author=Poet.named(author),
        language=(language or 'Unknown')[:50],
        source_url=source_url,
        source_site=website,
        **fields,
    )
    _add_tags(qasida, tags)
    return qasida


def _arabic_len(text):
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


def _text_shatter_score(text):
    """Fraction of Arabic tokens that are a single letter, plus the tatweel ratio."""
    tokens = [t for t in re.split(r'\s+', text or '') if ARABIC_RE.search(t)]
    if not tokens:
        return 1.0, 1.0, 0
    singles = sum(1 for t in tokens if len(t.strip('ـ')) <= 1)
    tatweel = (text.count('ـ') / len(text)) if text else 1.0
    return singles / len(tokens), tatweel, len(tokens)


def _looks_shattered(text):
    single_ratio, tatweel_ratio, tokens = _text_shatter_score(text)
    if tokens < 20:
        return False  # too little Arabic to judge
    return single_ratio > SHATTERED_SINGLE_RATIO or tatweel_ratio > SHATTERED_TATWEEL_RATIO


def _pdf_page_pngs(pdf_bytes, dpi=PAGE_IMAGE_DPI, limit=MAX_PDF_PAGES):
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


def _ocr_pdf(pdf_bytes):
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
        for _, png in _pdf_page_pngs(pdf_bytes, dpi=OCR_DPI):
            with Image.open(io.BytesIO(png)) as page_image:
                chunks.append(pytesseract.image_to_string(
                    page_image, lang=OCR_LANGS, config=config))
        text = unicodedata.normalize('NFKC', "\n".join(chunks)).strip()
        single_ratio, _, tokens = _text_shatter_score(text)
        # Prefer more recovered Arabic, then less shattering.
        key = (tokens, -single_ratio)
        if best_key is None or key > best_key:
            best, best_key = text, key
    return best


def _ocr_is_improvement(transcript, original):
    """
    Accept OCR only if it reads better *and* did not lose the content.

    Coverage is measured in Arabic letters rather than words: shattering splits
    one word into several tokens, so a token comparison would flatter OCR modes
    that simply recognise less. Letters survive shattering, so they compare
    like for like.
    """
    if not transcript:
        return False
    original_single, _, _ = _text_shatter_score(original)
    single, _, tokens = _text_shatter_score(transcript)
    if tokens < 20:
        return False
    original_letters = _arabic_len(original)
    if original_letters and _arabic_len(transcript) < OCR_MIN_COVERAGE * original_letters:
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


def _prepare_scan(image):
    """Upscale and sharpen a scan so Tesseract has more to work with."""
    grey = ImageOps.grayscale(image.convert('RGB'))
    larger = grey.resize(
        (grey.width * SCAN_OCR_UPSCALE, grey.height * SCAN_OCR_UPSCALE), Image.LANCZOS)
    return larger.filter(SCAN_OCR_SHARPEN)


def _keep_arabic_lines(text):
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
                    _prepare_scan(image), lang=OCR_LANGS, config=SCAN_OCR_CONFIG)
        except Exception as e:
            print(f"    scan OCR failed ({type(e).__name__}): {scan.image.name}")
            continue
        cleaned = _keep_arabic_lines(unicodedata.normalize('NFKC', raw))
        if cleaned:
            pages.append(cleaned)
    return "\n\n".join(pages).strip()


def _store_page_images(qasida, pdf_bytes, label='Page'):
    """Attach rasterised PDF pages as scans, skipping pages already stored."""
    added = 0
    for index, png in _pdf_page_pngs(pdf_bytes):
        marker = f"{qasida.source_url}#page={index + 1}"
        if qasida.images.filter(source_url=marker).exists():
            continue
        record = QasidaImage(qasida=qasida, source_url=marker,
                             caption=f"{label} {index + 1}", position=index)
        record.image.save(f"page-{qasida.pk}-{index + 1}.png",
                          ContentFile(png), save=True)
        added += 1
    return added


def _normalize_url(url):
    """
    Collapse doubled slashes in the path.

    The damas markup contains hrefs like "https://nur.nu//damas/..." which the
    origin answers with a 404, so the scan is lost unless the path is repaired.
    """
    scheme, separator, rest = url.partition('://')
    if not separator:
        return url
    return f"{scheme}://{re.sub(r'/{2,}', '/', rest)}"


def _clean_title(raw):
    """
    Turn a REST title fragment into plain text.

    Most of these carry only HTML entities. Handing a markup-free string to
    BeautifulSoup makes it warn that the input looks like a filename, so only
    parse when there is actually a tag to strip.
    """
    if '<' in raw:
        raw = BeautifulSoup(raw, 'html.parser').get_text(separator=' ')
    return html.unescape(raw).strip()


def _image_caption(url):
    """damas names its scans with a language suffix (..._Friendliness-en.jpg)."""
    stem = url.rsplit('/', 1)[-1]
    stem = IMAGE_EXT_RE.sub('', stem)
    if re.search(r'[-_](ar|arabic)$', stem, re.I):
        return 'Arabic'
    if re.search(r'[-_](en|english)$', stem, re.I):
        return 'English'
    if re.search(r'[-_](sv|swedish)$', stem, re.I):
        return 'Swedish'
    return ''


def _image_urls(body):
    """Pick the scan URLs out of a post body, preferring full-size originals."""
    soup = BeautifulSoup(body, 'html.parser')
    found = []
    for tag in soup.find_all(['img', 'a']):
        value = (tag.get('src') or tag.get('href') or '').strip()
        if not value.startswith('http'):
            continue  # data: placeholders used for lazy loading
        if not IMAGE_EXT_RE.search(value) or IMAGE_DENY_RE.search(value):
            continue
        found.append(_normalize_url(value))

    best = {}
    for url in found:
        original = WP_SIZE_SUFFIX_RE.sub(r'.\1', url)
        if original not in best or url == original:
            best[original] = url
    return list(dict.fromkeys(best.values()))


def _store_images(qasida, urls):
    """
    Download and attach any scans this qasida does not already hold.

    Returns the number added. Every candidate is decoded before it is stored:
    some of these paths answer 200 with an HTML error body, and the markup
    links icons alongside the real scans.
    """
    added = 0
    for position, url in enumerate(urls):
        if qasida.images.filter(source_url=url).exists() or _scan_was_rejected(url):
            continue
        try:
            data = polite_get(url, timeout=45, max_bytes=MAX_SCAN_BYTES).content
        except RateLimited:
            raise
        except Exception as e:
            print(f"    image failed ({type(e).__name__}): {url[:90]}")
            continue
        try:
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
            with Image.open(io.BytesIO(data)) as probe:
                width, height = probe.size
                image_format = (probe.format or '').lower()
        except Exception as e:
            print(f"    not an image ({type(e).__name__}): {url[:90]}")
            _remember_rejected_scan(url)
            continue
        if width < MIN_IMAGE_PX or height < MIN_IMAGE_PX:
            _remember_rejected_scan(url)
            continue

        extension = 'jpg' if image_format in ('jpeg', 'jpg') else (image_format or 'jpg')
        stem = slugify(IMAGE_EXT_RE.sub('', url.rsplit('/', 1)[-1]))[:60] or 'scan'
        digest = hashlib.sha1(url.encode('utf-8')).hexdigest()[:10]

        record = QasidaImage(
            qasida=qasida,
            source_url=url,
            caption=_image_caption(url),
            position=position,
        )
        record.image.save(f"{stem}-{digest}.{extension}", ContentFile(data), save=True)
        added += 1
    return added


def _rejected_scan_key(url):
    return f"scan-rejected:{hashlib.sha1(url.encode('utf-8')).hexdigest()}"


def _scan_was_rejected(url):
    try:
        return bool(cache.get(_rejected_scan_key(url)))
    except Exception:
        return False  # no cache, no memory: fetch it as before


def _remember_rejected_scan(url):
    """
    Note a URL that turned out not to be a scan, so it is not fetched nightly.

    Only for a file that arrived and was not a usable page. A network failure
    is not remembered, because the next attempt may well succeed.
    """
    try:
        cache.set(_rejected_scan_key(url), 1, REJECTED_SCAN_TTL)
    except Exception:
        pass


def _add_tags(qasida, names):
    """Attach tags by name, creating them as needed. Skips blanks and overlong slugs."""
    for name in dict.fromkeys(n for n in names if n):
        if len(name) > 50:
            continue
        tag, _ = Tag.objects.get_or_create(name=name)
        qasida.tags.add(tag)


def scrape_mynaatbook(website):
    """
    Scraper for mynaatbook.com which stores its data inside a React JS bundle.
    We fetch the JS bundle, extract the JSON-like objects using regex, and load them.
    """
    print(f"Scraping mynaatbook: {website.url}")

    try:
        # First, fetch the main page to find the main JS bundle
        main_page = polite_get(website.url, timeout=20)
        main_page.raise_for_status()

        # Look for the main JS bundle (e.g., /static/js/main.9c3048ce.js)
        js_match = re.search(r'src="(/static/js/main\.[a-f0-9]+\.js)"', main_page.text)
        if not js_match:
            print(f"Could not find main JS bundle on {website.url}")
            return

        js_url = f"{website.url.rstrip('/')}{js_match.group(1)}"
        print(f"Found JS bundle: {js_url}")

        response = polite_get(js_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching from {website.url}: {e}")
        return

    pattern = re.compile(r'\{"naat_name":"(.*?)","naat_body":\[(.*?)\].*?"naat_url":"(.*?)"')
    matches = pattern.findall(response.text)

    # The bundle repeats naats across its category listings, so collapse on
    # naat_url before reporting a count - otherwise the log overstates the total.
    unique = {}
    for name, body_str, url in matches:
        unique.setdefault(url, (name, body_str))
    print(f"Found {len(unique)} unique naats on mynaatbook "
          f"({len(matches)} raw matches, {len(matches) - len(unique)} duplicates in bundle).")

    stats = Counter()
    for url, (name, body_str) in unique.items():
        if Qasida.objects.filter(source_url=url).exists():
            stats['already_present'] += 1
            continue

        lyrics = "\n".join(re.findall(r'"([^"]*)"', body_str))
        if not lyrics:
            stats['no_lyrics'] += 1
            print(f"  SKIP (no lyrics in bundle): {name}")
            continue
        if not _safe_source_url(url):
            stats['bad_url'] += 1
            continue

        try:
            _create_work(website, title=name, lyrics=lyrics, source_url=url,
                         language='Urdu', tags=['naat', 'urdu'])
        except Exception as e:
            stats['errors'] += 1
            print(f"  failed ({type(e).__name__}): {name[:60]}")
            continue
        stats['saved'] += 1

    print(f"mynaatbook done: {stats['saved']} saved, {stats['already_present']} already present, "
          f"{stats['no_lyrics']} without lyrics, {stats['bad_url']} with an unusable URL, "
          f"{stats['errors']} errors.")


def scrape_desertechoblog(website):
    """
    Scraper for desertechoblog.wordpress.com
    We fetch the homepage, find all post links, and extract lyrics from entry-content.
    """
    print(f"Scraping desertechoblog: {website.url}")

    try:
        response = polite_get(website.url, timeout=20)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching {website.url}: {e}")
        return

    soup = BeautifulSoup(response.content, 'html.parser')
    host = urlsplit(website.url).hostname
    # Find all article/post links on the homepage. Only the blog's own: a
    # dated link to anywhere else is somebody else's post.
    post_links = [a['href'] for a in soup.select('a')
                  if a.has_attr('href') and ('/201' in a['href'] or '/202' in a['href'])
                  and _same_host(a['href'], host)]
    post_links = list(set(post_links))

    if not post_links:
        # Fallback if selectors above didn't catch anything, just grab links in the content area
        post_links = [a['href'] for a in soup.select('div#content a, main#main a')
                      if a.has_attr('href') and '/20' in a['href']
                      and _same_host(a['href'], host)]
        post_links = list(set(post_links))

    print(f"Found {len(post_links)} links on desertechoblog.")

    stats = Counter()
    for link in post_links:
        if Qasida.objects.filter(source_url=link).exists():
            stats['already_present'] += 1
            continue
        if not _safe_source_url(link):
            stats['bad_url'] += 1
            continue

        try:
            detail_res = polite_get(link, timeout=20)
            detail_soup = BeautifulSoup(detail_res.content, 'html.parser')

            title_tag = detail_soup.select_one('h1.entry-title')
            title = title_tag.get_text(strip=True) if title_tag else 'Unknown Title'

            content_div = detail_soup.select_one('div.entry-content')
            if not content_div:
                stats['no_content_div'] += 1
                print(f"  SKIP (no div.entry-content): {link}")
                continue

            # Remove sharing/like buttons if any
            for div in content_div.select('div.sharedaddy, div.wpcnt'):
                div.decompose()
            lyrics = content_div.get_text(separator='\n', strip=True)
            if not lyrics:
                stats['empty_content'] += 1
                print(f"  SKIP (empty content): {title}")
                continue

            _create_work(website, title=title, lyrics=lyrics, source_url=link,
                         language='Arabic',  # Assuming mostly Arabic Qasidas
                         tags=['qasida', 'arabic'])
            stats['saved'] += 1
        except Exception as e:
            stats['errors'] += 1
            print(f"Error processing {link}: {e}")

    print(f"desertechoblog done: {stats['saved']} saved, {stats['already_present']} already present, "
          f"{stats['no_content_div']} without a content div, {stats['empty_content']} empty, "
          f"{stats['errors']} errors.")


# --- damas.nur.nu -----------------------------------------------------------
#
# damas publishes its qasidas as PDFs and JPG scans shown through an embedded
# document viewer, so the post HTML holds almost no lyrics. Two things make it
# scrapeable anyway: the site runs a WordPress REST API that lists every qasida
# post (the public archive page only shows the first 50 of them), and the PDFs
# carry a real text layer. Posts whose only source is a JPG need OCR and are
# reported as skipped rather than silently dropped.

DAMAS_TAXONOMIES = ('maqam', 'bahr', 'qasida_type', 'language', 'tags')

# Taxonomies whose slugs are ambiguous on their own ("various", "rast"), so the
# tag keeps its taxonomy as a prefix.
DAMAS_PREFIXED = {'maqam', 'bahr'}


def _damas_api_base(website):
    parts = urlsplit(website.url)
    return f"{parts.scheme}://{parts.netloc}/wp-json/wp/v2"


def _damas_term_slugs(api_base):
    """Map term id -> tag name for each taxonomy, so posts can be tagged without _embed."""
    slugs = {}
    for taxonomy in DAMAS_TAXONOMIES:
        page = 1
        while True:
            try:
                res = polite_get(f"{api_base}/{taxonomy}", timeout=40, allow=(401, 403, 404),
                                 params={'per_page': 100, 'page': page})
                if res.status_code != 200:
                    # damas restricts some taxonomies (the stock 'tags' route
                    # answers 401), so note it and move on without those names.
                    if page == 1:
                        print(f"  damas taxonomy '{taxonomy}' unavailable (HTTP {res.status_code}); "
                              f"its terms will not be tagged.")
                    break
                terms = res.json()
            except Exception as e:
                print(f"  could not load damas taxonomy '{taxonomy}' page {page}: {type(e).__name__}")
                break
            if not isinstance(terms, list) or not terms:
                break
            for term in terms:
                if isinstance(term, dict) and term.get('id') and term.get('slug'):
                    prefix = f"{taxonomy}-" if taxonomy in DAMAS_PREFIXED else ""
                    slugs[(taxonomy, term['id'])] = f"{prefix}{term['slug']}"
            if len(terms) < 100:
                break
            page += 1
    return slugs


DAMAS_PAGE_SIZE = 25
# 25 / 5 == 5, so a failed page maps onto whole sub-pages with no gaps.
DAMAS_FALLBACK_PAGE_SIZE = 5


def _damas_fetch(api_base, per_page, page, attempts=3):
    """
    Fetch one page of qasida posts.

    Returns the list of posts, [] once past the last page, or None if the
    response never parsed as JSON.
    """
    for attempt in range(attempts):
        try:
            res = polite_get(f"{api_base}/qasida", timeout=60, allow=(400,),
                             params={'per_page': per_page, 'page': page})
            if res.status_code == 400:
                return []  # past the last page
            res.raise_for_status()
            batch = res.json()
            if isinstance(batch, list):
                return batch
        except Exception as e:
            print(f"  damas API page {page} (size {per_page}) attempt {attempt + 1} "
                  f"failed: {type(e).__name__}")
            time.sleep(2)
    return None


def _damas_posts(api_base):
    """
    Yield every qasida post from the REST API.

    Pages are kept small because this host serves a non-JSON error body for
    large slices - per_page=100 fails outright, and even at 25 one slice of the
    archive never serializes. The same posts come back fine in smaller requests,
    so a failed page is retried in DAMAS_FALLBACK_PAGE_SIZE chunks before we
    give up on it.
    """
    page = 1
    while True:
        recovered = False
        batch = _damas_fetch(api_base, DAMAS_PAGE_SIZE, page)

        if batch is None:
            print(f"  damas API page {page}: retrying that range in "
                  f"{DAMAS_FALLBACK_PAGE_SIZE}-post chunks.")
            recovered = True
            batch = []
            step = DAMAS_PAGE_SIZE // DAMAS_FALLBACK_PAGE_SIZE
            for sub in range(step * (page - 1) + 1, step * page + 1):
                chunk = _damas_fetch(api_base, DAMAS_FALLBACK_PAGE_SIZE, sub, attempts=2)
                if chunk is None:
                    print(f"    sub-page {sub} still failing; up to "
                          f"{DAMAS_FALLBACK_PAGE_SIZE} posts deferred to the next run.")
                    continue
                batch.extend(chunk)
            print(f"  damas API page {page}: recovered {len(batch)} of "
                  f"{DAMAS_PAGE_SIZE} posts.")

        if not batch:
            return

        for post in batch:
            yield post

        # A recovered page can be short because a sub-page failed, so its
        # length is not a reliable end-of-list signal.
        if not recovered and len(batch) < DAMAS_PAGE_SIZE:
            return
        page += 1
        time.sleep(1)


def _pick_arabic_pdf(urls):
    """Prefer the Arabic edition of a qasida PDF, else the first one offered."""
    for url in urls:
        if re.search(r'[-_](ar|arabic)[-_.]', url, re.I):
            return url
    return urls[0] if urls else None


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


def _reassemble_pdf_text(pdf_bytes):
    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    try:
        text = "\n".join(_reassemble_page(page) for page in doc)
    finally:
        doc.close()
    return unicodedata.normalize('NFKC', text).strip()


def _reassembly_is_improvement(rebuilt, original):
    """Accept the rebuild only if it reads better and kept the letters."""
    if not rebuilt or _looks_shattered(rebuilt):
        return False
    original_letters = len(_letters_only(original))
    if original_letters and len(_letters_only(rebuilt)) < REASSEMBLY_MIN_COVERAGE * original_letters:
        return False
    return True


def _pdf_text(pdf_bytes):
    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    # These PDFs use subset fonts that emit Arabic presentation forms - NFKC
    # folds those back to normal letters.
    return unicodedata.normalize('NFKC', text).strip()


def _fetch_pdf_text(url):
    """
    Return (pdf bytes, extracted text).

    The bytes are kept so a document whose text layer turns out to be
    unreadable can be rasterised and OCR'd instead of discarded.
    """
    res = polite_get(url, timeout=45, max_bytes=MAX_PDF_BYTES)
    if not res.content.startswith(b'%PDF'):
        return None, ''
    return res.content, _pdf_text(res.content)


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


def _strip_viewer_chrome(text):
    lines = [ln for ln in text.splitlines() if ln.strip() not in VIEWER_CHROME]
    return "\n".join(lines).strip()


def _import_damas_post(website, post, term_slugs, stats):
    """Store one damas post, or top up the scans of one already held."""
    stats['seen'] += 1
    link = post.get('link') or ''
    raw_title = _clean_title(post.get('title', {}).get('rendered', ''))
    title, native_title, author = split_title(raw_title)
    title = title or 'Unknown Title'

    if not link:
        stats['no_link'] += 1
        return
    if not _safe_source_url(link):
        stats['bad_url'] += 1
        return

    body = post.get('content', {}).get('rendered', '') or ''
    image_urls = _image_urls(body)

    existing = Qasida.objects.filter(source_url=link).first()
    if existing:
        # Still fetch the scans - rows saved before images were collected
        # need backfilling, and a post can gain pages later.
        stats['already_present'] += 1
        added = _store_images(existing, image_urls)
        if added:
            stats['images_backfilled'] += added
            print(f"  +{added} scan(s) for existing: {title[:60]}")
        return

    soup = BeautifulSoup(body, 'html.parser')

    # 1. A handful of legacy posts still carry the text inline.
    lyrics, origin = '', ''
    pdf_bytes = None
    legacy = soup.select_one('div.arabic')
    if legacy:
        lyrics, origin = legacy.get_text(separator='\n', strip=True), 'inline div.arabic'

    # 2. Otherwise pull the text layer out of the linked PDF.
    if _arabic_len(lyrics) < MIN_ARABIC_CHARS:
        pdf_url = _pick_arabic_pdf(list(dict.fromkeys(PDF_URL_RE.findall(body))))
        if pdf_url:
            try:
                pdf_bytes, candidate = _fetch_pdf_text(pdf_url)
                if _arabic_len(candidate) > _arabic_len(lyrics):
                    lyrics, origin = candidate, f"pdf {pdf_url.rsplit('/', 1)[-1]}"
            except RateLimited:
                raise
            except Exception as e:
                stats['pdf_errors'] += 1
                print(f"  PDF failed for {title[:60]}: {type(e).__name__}")

    # 3. Last resort: whatever text the post itself renders.
    if _arabic_len(lyrics) < MIN_ARABIC_CHARS:
        inline = _strip_viewer_chrome(soup.get_text(separator='\n', strip=True))
        if _arabic_len(inline) > _arabic_len(lyrics):
            lyrics, origin = inline, 'inline post text'

    # When there is no machine-readable text, the scans are the content:
    # keep the post and show the images rather than dropping it.
    scan_only = _arabic_len(lyrics) < MIN_ARABIC_CHARS
    if scan_only and not image_urls:
        stats['no_text'] += 1
        print(f"  SKIP (no lyrics and no scans): {title[:70]}")
        return

    # Real taxonomy from the site instead of two hardcoded tags.
    tag_names = ['qasida']
    for taxonomy in DAMAS_TAXONOMIES:
        for term_id in post.get(taxonomy) or []:
            name = term_slugs.get((taxonomy, term_id))
            if name:
                tag_names.append(name)

    languages = [term_slugs.get(('language', tid)) for tid in post.get('language') or []]
    languages = [lang for lang in languages if lang]
    if 'arabic' in languages:
        language = 'Arabic'
    elif languages:
        language = languages[0].title()
    else:
        language = 'Arabic'

    # A readable-looking text layer can still be shattered glyph soup. When
    # it is, rasterise the pages (the trustworthy record) and OCR them.
    text_quality = Qasida.TEXT_OK
    if pdf_bytes and _looks_shattered(lyrics):
        text_quality = Qasida.TEXT_POOR
        rebuilt = ''
        try:
            rebuilt = _reassemble_pdf_text(pdf_bytes)
        except Exception as e:
            print(f"  reassembly failed for {title[:60]}: {type(e).__name__}")
        if _reassembly_is_improvement(rebuilt, lyrics):
            lyrics, origin = rebuilt, 'reflowed'
            text_quality = Qasida.TEXT_OCR
        else:
            transcript = ''
            try:
                transcript = _ocr_pdf(pdf_bytes)
            except Exception as e:
                print(f"  OCR failed for {title[:60]}: {type(e).__name__}")
            if _ocr_is_improvement(transcript, lyrics):
                lyrics, origin = transcript, 'ocr'
                text_quality = Qasida.TEXT_OCR
        stats['shattered'] += 1

    if scan_only:
        tag_names.append(SCAN_ONLY_TAG)
    if text_quality != Qasida.TEXT_OK:
        tag_names.append(UNRELIABLE_TEXT_TAG)

    qasida = _create_work(
        website,
        title=title,
        native_title=native_title,
        # A name from a source becomes a record of that poet, reusing
        # one we already hold rather than repeating the name per work.
        author=author,
        lyrics=lyrics if not scan_only else _strip_viewer_chrome(
            soup.get_text(separator='\n', strip=True)),
        source_url=link,
        language=language,
        text_quality=text_quality,
        tags=tag_names,
    )

    added = _store_images(qasida, image_urls)
    if text_quality != Qasida.TEXT_OK and pdf_bytes:
        # Without a reliable transcription the rendered pages are the content.
        added += _store_page_images(qasida, pdf_bytes)
    stats['images_saved'] += added
    if scan_only:
        if not added:
            # Every candidate failed to decode, so there is nothing to show.
            qasida.delete()
            stats['image_fetch_failed'] += 1
            print(f"  SKIP (scans unreadable): {title[:70]}")
            return
        stats['saved_scan_only'] += 1
        origin = f"{added} scan(s)"
    else:
        stats['saved'] += 1
        if added:
            origin = f"{origin} + {added} scan(s)"
    print(f"Scraped and saved [{origin}]: {title[:70]}")



def scrape_damas(website):
    """Scraper for damas.nur.nu, driven by its WordPress REST API."""
    print(f"Scraping damas: {website.url}")
    api_base = _damas_api_base(website)

    term_slugs = _damas_term_slugs(api_base)
    print(f"Loaded {len(term_slugs)} damas taxonomy terms.")

    stats = Counter()
    refusals = _Refusals()
    for post in _damas_posts(api_base):
        try:
            _import_damas_post(website, post, term_slugs, stats)
            refusals.answered()
        except RateLimited as e:
            stats['refused'] += 1
            print(f"  refused: {e}")
            refusals.refused(e)
        except BotChallenge:
            raise
        except Exception as e:
            # One post the code did not foresee is logged and passed over, so
            # it cannot stop every post after it, night after night.
            stats['errors'] += 1
            print(f"  failed ({type(e).__name__}): {(post.get('link') or '')[:90]}")

    print(f"damas done: {stats['seen']} posts seen, {stats['saved']} saved with text, "
          f"{stats['saved_scan_only']} saved as scans only, {stats['already_present']} already present, "
          f"{stats['images_saved'] + stats['images_backfilled']} scans stored "
          f"({stats['images_backfilled']} backfilled), {stats['no_text']} without text or scans, "
          f"{stats['image_fetch_failed']} with unreadable scans, {stats['shattered']} with an "
          f"unreadable text layer (rasterised + OCR'd), {stats['pdf_errors']} PDF errors, "
          f"{stats['bad_url']} with an unusable URL, {stats['refused']} refused, "
          f"{stats['errors']} errors.")


# --- lyrics.midhah.com ------------------------------------------------------
#
# A Next.js site whose class names are build-hashed, so the markup is a poor
# extraction target. Every page instead carries a schema.org MusicComposition
# in a JSON-LD block, giving the title, genre, language, poet and the lyric
# text with its stanza breaks intact - plus workTranslation.url pointing at a
# matching Latin transliteration. robots.txt allows everything but /collection/
# and declares the sitemap we enumerate from.

MIDHAH_SECTIONS = ('naat', 'manqbat', 'hamd', 'durood-o-salam', 'sufiyana-kalam')

# Language codes the source uses, mapped to the names shown in the library.
MIDHAH_LANGUAGES = {
    'ur': 'Urdu', 'ar': 'Arabic', 'fa': 'Persian', 'pa': 'Punjabi', 'en': 'English',
}


def _midhah_composition(url):
    """Return the MusicComposition JSON-LD object from a midhah page."""
    soup = BeautifulSoup(polite_get(url, timeout=40).content, 'html.parser')
    for block in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(block.string or '')
        except (ValueError, TypeError):
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and item.get('@type') == 'MusicComposition':
                return item
    return None


def _midhah_lyric_urls(base_url):
    """Enumerate lyric pages from the sitemap, skipping poet and utility pages."""
    parts = urlsplit(base_url)
    root = f"{parts.scheme}://{parts.netloc}"
    try:
        sitemap = polite_get(f"{root}/sitemap.xml", timeout=90).text
    except Exception as e:
        print(f"  could not read the midhah sitemap: {type(e).__name__}")
        return []
    locations = re.findall(r'<loc>([^<]+)</loc>', sitemap)
    pattern = re.compile(
        rf"^{re.escape(root)}/({'|'.join(MIDHAH_SECTIONS)})/[^/]+$")
    return [u for u in dict.fromkeys(locations) if pattern.match(u)]


def _ld_url(value):
    """A URL out of a JSON-LD field that may be a string, an object or a list."""
    if isinstance(value, list):
        value = value[0] if value else ''
    if isinstance(value, dict):
        value = value.get('url') or ''
    return value if isinstance(value, str) else ''


def _import_midhah_page(website, url, host, stats):
    """
    Store the work on one midhah lyric page.

    schema.org lets every field used here be a string, an object or a list,
    and a page that picked a different one than expected used to raise out
    of the crawl. They are read through _as_text, which accepts all three.
    """
    if not _safe_source_url(url):
        stats['bad_url'] += 1
        return
    try:
        composition = _midhah_composition(url)
    except RateLimited:
        raise  # counted by the caller, which parks the source if it keeps up
    except Exception as e:
        stats['errors'] += 1
        print(f"  fetch failed ({type(e).__name__}): {url}")
        return

    if not composition:
        stats['no_metadata'] += 1
        print(f"  SKIP (no JSON-LD composition): {url}")
        return

    lyrics = _as_text(composition.get('lyrics'))
    if not lyrics:
        stats['no_lyrics'] += 1
        print(f"  SKIP (no lyric text): {url}")
        return

    # The Latin version lives on its own page, keyed by workTranslation. Only
    # followed on midhah itself: it is a link out of the page's own data.
    transliteration = ''
    translation_url = _ld_url(composition.get('workTranslation'))
    if translation_url and _same_host(translation_url, host):
        try:
            other = _midhah_composition(translation_url)
            if other:
                transliteration = _as_text(other.get('lyrics'))
        except RateLimited:
            raise
        except Exception as e:
            stats['translation_errors'] += 1
            print(f"  transliteration failed ({type(e).__name__}): {url}")

    genre = _as_text(composition.get('genre'))
    poet = _as_text(composition.get('lyricist'))
    code = _as_text(composition.get('inLanguage')).lower()
    language = MIDHAH_LANGUAGES.get(code, code.title() or 'Urdu')

    title, native_title, author = split_title(_clean_title(_as_text(composition.get('name'))))

    tag_names = [language.lower()]
    if genre:
        tag_names.append(slugify(genre))
    if transliteration:
        tag_names.append('transliterated')
    _create_work(website, title=title, native_title=native_title,
                 author=poet or author, language=language, lyrics=lyrics,
                 transliteration=transliteration, source_url=url, tags=tag_names)

    stats['saved'] += 1
    if transliteration:
        stats['with_transliteration'] += 1


def scrape_midhah(website, limit=None):
    """
    Scraper for lyrics.midhah.com, driven by its JSON-LD and sitemap.

    `limit` caps how many new pages are taken in one pass, which keeps a first
    run short enough to check before committing to the whole sitemap.
    """
    print(f"Scraping midhah: {website.url}")
    urls = _midhah_lyric_urls(website.url)
    print(f"Found {len(urls)} lyric pages in the midhah sitemap.")

    # Fetch the URLs already held in one query rather than one per page: this
    # runs over thousands of candidates and is re-run to resume.
    known = set(Qasida.objects.filter(source_site=website)
                .values_list('source_url', flat=True))

    host = urlsplit(website.url).hostname
    stats = Counter()
    refusals = _Refusals()
    for url in urls:
        if limit is not None and stats['saved'] >= limit:
            print(f"Stopping at the {limit}-page limit for this pass.")
            break
        if url in known:
            stats['already_present'] += 1
            continue
        try:
            _import_midhah_page(website, url, host, stats)
            refusals.answered()
        except RateLimited as e:
            stats['refused'] += 1
            print(f"  refused: {url} ({e})")
            refusals.refused(e)
        except BotChallenge:
            raise
        except Exception as e:
            # One page shaped in a way nobody foresaw is logged and passed
            # over rather than stopping the source at it every night.
            stats['errors'] += 1
            print(f"  failed ({type(e).__name__}): {url}")

    print(f"midhah done: {stats['saved']} saved "
          f"({stats['with_transliteration']} with a transliteration), "
          f"{stats['already_present']} already present, {stats['no_metadata']} without JSON-LD, "
          f"{stats['no_lyrics']} without lyric text, {stats['errors']} fetch errors, "
          f"{stats['translation_errors']} transliteration errors, {stats['refused']} refused, "
          f"{stats['bad_url']} with an unusable URL.")


# --- generic source ---------------------------------------------------------
#
# For a site with no bespoke parser. Pages are discovered from the sitemap when
# there is one, else by following links within the host, and each is handed to
# core.extract, which prefers schema.org JSON-LD and falls back to the densest
# block of text. robots.txt is honoured because this can be pointed anywhere.

# Ceilings so a badly chosen start URL cannot crawl forever.
GENERIC_MAX_PAGES = 400
GENERIC_MAX_DISCOVERY = 2000
GENERIC_MAX_SITEMAPS = 20

# Paths that are never a single work.
GENERIC_SKIP_RE = re.compile(
    r'/(tag|tags|category|categories|author|authors|page|search|feed|wp-json|'
    r'wp-admin|wp-content|login|register|privacy|terms|about|contact)(/|$)', re.I)


def _robots_for(root):
    """Load robots.txt. A site that will not serve it is treated as open."""
    parser = RobotFileParser()
    try:
        response = polite_get(f"{root}/robots.txt", timeout=25, allow=(401, 403, 404, 429))
        if response.status_code == 200:
            parser.parse(response.text.splitlines())
        else:
            print(f"  robots.txt returned {response.status_code}; proceeding carefully")
            parser.allow_all = True
    except BotChallenge:
        raise
    except Exception as e:
        print(f"  robots.txt unavailable ({type(e).__name__}); proceeding carefully")
        parser.allow_all = True
    return parser


def _sitemap_urls(root):
    """Collect page URLs from sitemap.xml, following one level of index."""
    collected = []
    queue = [f"{root}/sitemap.xml"]
    seen = set()
    host = urlsplit(root).hostname
    # Counted as they are queued, not as they are read: an index listing
    # thousands of sub-sitemaps used to queue every one while this was still
    # at one, and then spend hours reading them.
    sitemaps_queued = 1
    while queue and len(collected) < GENERIC_MAX_DISCOVERY:
        target = queue.pop(0)
        if target in seen:
            continue
        seen.add(target)
        try:
            body = polite_get(target, timeout=60, allow=(404,))
        except BotChallenge:
            raise
        except Exception:
            continue
        if body.status_code != 200:
            continue
        locations = re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', body.text)
        for location in locations:
            if not _same_host(location, host):
                continue
            if location.endswith('.xml'):
                if sitemaps_queued < GENERIC_MAX_SITEMAPS:
                    queue.append(location)
                    sitemaps_queued += 1
            else:
                collected.append(location)
    return collected


# Marks a page the link crawl fetched but could not extract, so the import
# fetches it again and reports the failure in its own count.
_NOT_EXTRACTED = object()


def _crawl_links(root, start_url, robots, budget):
    """
    Breadth-first link discovery, used when there is no sitemap.

    Returns the pages found, each with what extraction made of it, so the
    import that follows does not download every page a second time. Only the
    small extracted result is kept, not the page.
    """
    host = urlsplit(root).hostname
    found, seen, queue = {}, {start_url}, [start_url]
    while queue and len(found) < budget:
        current = queue.pop(0)
        try:
            page = polite_get(current, timeout=30)
        except BotChallenge:
            raise
        except Exception:
            continue
        try:
            found[current] = extract_work(page.text)
        except Exception:
            found[current] = _NOT_EXTRACTED
        soup = BeautifulSoup(page.content, 'html.parser')
        for anchor_tag in soup.select('a[href]'):
            link = urljoin(current, anchor_tag['href']).split('#')[0]
            if not _same_host(link, host) or link in seen:
                continue
            if GENERIC_SKIP_RE.search(link) or not robots.can_fetch(USER_AGENT, link):
                continue
            seen.add(link)
            if len(seen) < GENERIC_MAX_DISCOVERY:
                queue.append(link)
    return found


def _import_generic_page(website, url, work, stats):
    """Store the work on one page, fetching it unless the link crawl already did."""
    if not _safe_source_url(url):
        stats['bad_url'] += 1
        return
    if work is _NOT_EXTRACTED:
        try:
            page = polite_get(url, timeout=30)
        except RateLimited:
            raise
        except Exception:
            stats['fetch_errors'] += 1
            return
        try:
            work = extract_work(page.text)
        except Exception as e:
            stats['extract_errors'] += 1
            print(f"  extract failed ({type(e).__name__}): {url}")
            return

    if not work:
        stats['nothing_found'] += 1
        return

    title, native_title, author = split_title(_clean_title(work['title']))
    language = work['language'] or ('Arabic' if _arabic_len(work['lyrics']) > 40 else '')
    names = [slugify(work['genre'])] if work['genre'] else []
    if language:
        names.append(slugify(language))
    _create_work(website, title=title, native_title=native_title,
                 author=work['author'] or author, language=language,
                 lyrics=work['lyrics'], source_url=url, tags=names)
    stats['saved'] += 1
    stats[f"via_{work['via']}"] += 1


def scrape_generic(website, limit=None):
    """Crawl any site, extracting whatever structure it exposes."""
    print(f"Scraping (generic): {website.url}")
    parts = urlsplit(website.url)
    root = f"{parts.scheme}://{parts.netloc}"
    robots = _robots_for(root)

    if not robots.can_fetch(USER_AGENT, website.url):
        print(f"  robots.txt disallows {website.url}; nothing crawled.")
        return

    host = urlsplit(root).hostname
    extracted = {}
    candidates = [u for u in _sitemap_urls(root)
                  if _same_host(u, host) and not GENERIC_SKIP_RE.search(u)
                  and robots.can_fetch(USER_AGENT, u)]
    if candidates:
        print(f"  sitemap gave {len(candidates)} candidate pages")
    else:
        extracted = _crawl_links(root, website.url, robots, GENERIC_MAX_PAGES)
        candidates = list(extracted)
        print(f"  no usable sitemap; link crawl found {len(candidates)} pages")

    ceiling = limit if limit is not None else GENERIC_MAX_PAGES
    stats = Counter()
    refusals = _Refusals()
    for url in candidates:
        if stats['saved'] >= ceiling:
            print(f"  stopping at {ceiling} saved works for this pass")
            break
        if Qasida.objects.filter(source_url=url).exists():
            stats['already_present'] += 1
            continue
        try:
            _import_generic_page(website, url, extracted.get(url, _NOT_EXTRACTED), stats)
            refusals.answered()
        except RateLimited as e:
            stats['refused'] += 1
            refusals.refused(e)
        except BotChallenge:
            raise
        except Exception as e:
            stats['errors'] += 1
            print(f"  failed ({type(e).__name__}): {url}")

    print(f"generic done for {website.name}: {stats['saved']} saved "
          f"(json-ld {stats['via_json-ld']}, markup {stats['via_markup']}), "
          f"{stats['already_present']} already present, {stats['nothing_found']} with no work found, "
          f"{stats['fetch_errors']} fetch errors, {stats['extract_errors']} extract errors, "
          f"{stats['refused']} refused, {stats['errors']} errors.")


# --- Internet Archive snapshots --------------------------------------------
#
# For a site that cannot be fetched directly - qasidacollection sits behind a
# Vercel challenge that answers every path with a 429. The Internet Archive
# holds crawls of it from before that was switched on, and reading from the
# archive goes through a separate public service rather than defeating the
# origin's protection. Text recovered this way is a snapshot and its verse
# structure is often flattened, so it lands as pending review like everything
# else.

WAYBACK_CDX = 'http://web.archive.org/cdx/search/cdx'
WAYBACK_SNAPSHOT = 'https://web.archive.org/web/{timestamp}id_/{url}'
WAYBACK_MAX_URLS = 4000
# The archive throttles in bursts, so a refusal pauses rather than aborting.
WAYBACK_MAX_THROTTLES = 6
WAYBACK_THROTTLE_PAUSE = 30
# Paths that are not a single work.
WAYBACK_SKIP_RE = re.compile(
    r'/(_next|_vercel|\.well-known|static|assets|api|author|authors|tag|tags|'
    r'about|privacy|terms|contact|search|login)(/|$)', re.I)
# A two-letter opening segment on these sites is a locale, and the same work
# repeats under each one.
LOCALE_SEGMENT_RE = re.compile(r'^/[a-z]{2}(/|$)')


def _wayback_candidates(host):
    """Distinct archived content URLs for a host, newest snapshot of each."""
    try:
        response = polite_get(WAYBACK_CDX, timeout=120, params={
            'url': f'{host}/*',
            'fl': 'original,timestamp',
            'collapse': 'urlkey',
            'filter': 'statuscode:200',
            'limit': str(WAYBACK_MAX_URLS),
        })
    except Exception as e:
        print(f"  could not query the archive index: {type(e).__name__}")
        return []

    seen = {}
    for line in response.text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        url, timestamp = parts
        if '?' in url or WAYBACK_SKIP_RE.search(url):
            continue
        path = re.sub(r'^https?://[^/]+', '', url)
        if not path or path == '/':
            continue
        # Collapse locale duplicates onto one canonical path, keeping the
        # unprefixed URL when the archive has it.
        canonical = LOCALE_SEGMENT_RE.sub('/', path)
        previous = seen.get(canonical)
        if previous is None or (path == canonical and previous[2] != canonical):
            seen[canonical] = (url, timestamp, path)
    return [(url, timestamp) for url, timestamp, _ in seen.values()]


def scrape_wayback(website, limit=None, refresh=False):
    """
    Import a blocked site's works from Internet Archive snapshots.

    With `refresh`, rows already held are re-extracted and updated in place -
    used after an extraction fix, so existing records gain the corrected line
    structure without being deleted and re-created.
    """
    parts = urlsplit(website.url)
    host = parts.netloc or website.url
    print(f"Scraping archive snapshots of: {host}")

    candidates = _wayback_candidates(host)
    print(f"Found {len(candidates)} archived content URLs.")

    known = dict(Qasida.objects.filter(source_site=website)
                 .values_list('source_url', 'pk'))

    stats = Counter()
    throttled = 0
    for url, timestamp in candidates:
        if limit is not None and stats['saved'] >= limit:
            print(f"Stopping at the {limit}-work limit for this pass.")
            break
        # Attribution points at the original page, not the archive copy.
        existing = known.get(url)
        if existing is not None and not refresh:
            stats['already_present'] += 1
            continue

        snapshot = WAYBACK_SNAPSHOT.format(timestamp=timestamp, url=url)
        try:
            page = polite_get(snapshot, timeout=90)
            throttled = 0
        except RateLimited:
            # The archive throttles in bursts. Wait longer and carry on rather
            # than losing the rest of the import; give up only if it keeps
            # refusing, and the run is resumable either way.
            throttled += 1
            stats['throttled'] += 1
            if throttled >= WAYBACK_MAX_THROTTLES:
                print(f"  archive still throttling after {throttled} tries in a row; "
                      f"stopping here. Re-run to continue.")
                break
            wait = WAYBACK_THROTTLE_PAUSE * throttled
            print(f"  archive throttled ({throttled}/{WAYBACK_MAX_THROTTLES}); "
                  f"pausing {wait}s")
            time.sleep(wait)
            continue
        except Exception:
            stats['fetch_errors'] += 1
            continue

        try:
            _import_wayback_page(website, url, page, existing, stats)
        except Exception as e:
            stats['errors'] += 1
            print(f"  failed ({type(e).__name__}): {url}")

    print(f"archive import for {website.name}: {stats['saved']} saved, "
          f"{stats['already_present']} already present, {stats['refreshed']} refreshed, "
          f"{stats['refresh_no_gain']} refreshed with no gain, "
          f"{stats['nothing_found']} with no work found, {stats['throttled']} throttled, "
          f"{stats['fetch_errors']} fetch errors, {stats['extract_errors']} extract errors, "
          f"{stats['errors']} errors.")


def _import_wayback_page(website, url, page, existing, stats):
    """Store, or refresh, the work read from one archived snapshot."""
    try:
        work = extract_work(page.text)
    except Exception:
        stats['extract_errors'] += 1
        return

    if not work:
        stats['nothing_found'] += 1
        return

    title, native_title, author = split_title(_clean_title(work['title']))
    language = work['language'] or ('Arabic' if _arabic_len(work['lyrics']) > 40 else 'Unknown')

    if existing is not None:
        # Only the extracted text is refreshed; anything an editor set is
        # left alone, and the row keeps its review state.
        row = Qasida.objects.get(pk=existing)
        before = len([l for l in row.lyrics.splitlines() if l.strip()])
        after = len([l for l in work['lyrics'].splitlines() if l.strip()])
        if after > before:
            row.lyrics = work['lyrics']
            row.save(update_fields=['lyrics', 'search_text'])
            stats['refreshed'] += 1
            print(f"  refreshed {row.pk}: {before} -> {after} lines")
        else:
            stats['refresh_no_gain'] += 1
        return

    if not _safe_source_url(url):
        stats['bad_url'] += 1
        return
    names = ['from-archive']
    if work['genre']:
        names.append(slugify(work['genre']))
    if language and language != 'Unknown':
        names.append(slugify(language))
    _create_work(website, title=title, native_title=native_title,
                 author=work['author'] or author, language=language,
                 lyrics=work['lyrics'], source_url=url, tags=names)
    stats['saved'] += 1


# --- WordPress REST API source ----------------------------------------------

# A WordPress install that leaves its REST API open hands back the post body
# without the theme wrapped around it: no navigation, sidebar, share widget or
# related-posts block to strip, and the taxonomy arrives as ids that resolve to
# names. That is worth preferring over reading the rendered page.
WP_PAGE_SIZE = 100

# Site furniture that some installs leave inside the body itself.
WP_JUNK_RE = re.compile(
    r'^(download|share this|related posts?|subscribe|follow us|read more)\b', re.I)


def _wp_get(root, path, **params):
    """
    One collection from a WordPress REST API, or None once the pages run out.

    WordPress answers a page number past the last one with a 400 rather than an
    empty list, so that status is asked for rather than raised on: it is how the
    paging loops below know they have finished.
    """
    query = '&'.join(f'{key}={value}' for key, value in params.items())
    response = polite_get(f"{root}/wp-json/wp/v2/{path}?{query}",
                          timeout=60, allow=(400,))
    if response.status_code == 400:
        return None
    return response.json()


def _wp_terms(root, taxonomy):
    """{id: name} for one taxonomy, read once rather than per post."""
    terms, page = {}, 1
    while True:
        batch = _wp_get(root, taxonomy, per_page=WP_PAGE_SIZE, page=page)
        if not batch:
            return terms
        for term in batch:
            terms[term['id']] = _clean_title(term.get('name', ''))
        if len(batch) < WP_PAGE_SIZE:
            return terms
        page += 1


def _wp_body(rendered, title):
    """
    The verse from a REST content field, with its line structure intact.

    A lyric's line breaks live in <br>, which get_text alone throws away, so
    those become newlines first. Blank lines are collapsed to one rather than
    dropped, because the gap between stanzas is part of the poem. The post also
    repeats its own title as a heading ("<title> Lyrics"), which is already held
    in the title field and would otherwise open every text.
    """
    soup = BeautifulSoup(rendered or '', 'html.parser')
    for junk in soup.select('script, style, iframe, ins, .sharedaddy, .jp-relatedposts'):
        junk.decompose()

    wanted = {title.strip().lower(), f'{title.strip().lower()} lyrics'}
    for heading in soup.find_all(['h1', 'h2', 'h3']):
        if _clean_title(heading.get_text()).lower() in wanted:
            heading.decompose()

    for line_break in soup.find_all('br'):
        line_break.replace_with('\n')

    lines, pending_break = [], False
    for raw in soup.get_text('\n').split('\n'):
        line = raw.strip()
        if not line:
            pending_break = bool(lines)
            continue
        if WP_JUNK_RE.match(line):
            continue
        if pending_break:
            lines.append('')
            pending_break = False
        lines.append(line)
    return '\n'.join(lines).strip()


def scrape_wordpress_api(website, limit=None):
    """
    Scraper for any WordPress site that leaves its REST API open.

    This source publishes romanised Urdu rather than the Arabic-script
    original, so the text is stored as a transliteration and `lyrics` is left
    empty for an original-script copy to be paired with later. Storing Latin
    text as the lyrics would put it where the naskh and nastaliq faces and the
    right-to-left styling expect Arabic script.

    Everything lands pending, like every other crawled source: nothing here is
    published until a person has read it.
    """
    parts = urlsplit(website.url)
    root = f"{parts.scheme}://{parts.netloc}"
    print(f"Scraping WordPress API: {root}")

    categories = _wp_terms(root, 'categories')
    print(f"Vocabulary: {len(categories)} categories.")

    # The URLs already held, in one query rather than one per post: this runs
    # over hundreds of candidates and is re-run to resume.
    known = set(Qasida.objects.filter(source_site=website)
                .values_list('source_url', flat=True))

    # So the whole source can be found, filtered and withdrawn as one group.
    source_tag = slugify(website.name)

    stats = Counter()
    page = 1
    while True:
        if limit is not None and stats['saved'] >= limit:
            print(f"Stopping at the {limit}-post limit for this pass.")
            break
        try:
            posts = _wp_get(root, 'posts', per_page=WP_PAGE_SIZE, page=page)
        except RateLimited:
            raise  # let run_crawlers park this source for the next run
        except Exception as e:
            stats['errors'] += 1
            print(f"  page {page} failed ({type(e).__name__})")
            break
        if not posts:
            break

        for post in posts:
            if limit is not None and stats['saved'] >= limit:
                break
            link = post.get('link') or ''
            if not link:
                stats['no_link'] += 1
                continue
            if link in known:
                stats['already_present'] += 1
                continue

            title = _clean_title((post.get('title') or {}).get('rendered', ''))
            body = _wp_body((post.get('content') or {}).get('rendered', ''), title)
            if not body:
                stats['no_text'] += 1
                print(f"  SKIP (no text): {link}")
                continue

            if not _safe_source_url(link):
                stats['bad_url'] += 1
                continue

            name, native_title, author = split_title(title)
            tag_names = ['urdu', 'transliterated', NO_ORIGINAL_TAG, source_tag]
            tag_names += [slugify(categories[cid])
                          for cid in post.get('categories') or []
                          if cid in categories]
            try:
                _create_work(
                    website,
                    title=name,
                    native_title=native_title,
                    # The site credits performers rather than poets, and a
                    # performer is not the author, so this is left for a
                    # reviewer rather than guessed from the URL.
                    author=author,
                    language='Urdu',
                    lyrics='',
                    transliteration=body,
                    source_url=link,
                    tags=tag_names,
                )
            except Exception as e:
                stats['errors'] += 1
                print(f"  failed ({type(e).__name__}): {link}")
                continue

            known.add(link)
            stats['saved'] += 1

        if len(posts) < WP_PAGE_SIZE:
            break
        page += 1

    print(f"{website.name} done: {stats['saved']} saved, "
          f"{stats['already_present']} already present, "
          f"{stats['no_text']} without text, {stats['no_link']} without a link, "
          f"{stats['errors']} errors.")


@shared_task
def enrich_qasida(qasida_id, overwrite=False):
    """
    Derive transliteration and translation for one qasida.

    Runs on the worker because translating a poem line by line takes seconds,
    which is too slow to hold an admin form open for.
    """
    from .enrich import enrich

    try:
        qasida = Qasida.objects.get(pk=qasida_id)
    except Qasida.DoesNotExist:
        return f"qasida {qasida_id} no longer exists"
    changed = enrich(qasida, overwrite=overwrite)
    return f"qasida {qasida_id}: {', '.join(changed) if changed else 'nothing to add'}"


# How long one crawl may run before the worker kills it. The schedule starts a
# crawl every midnight, so one still going the following evening has stalled,
# and would otherwise run into the next.
CRAWL_TIME_LIMIT = 20 * 60 * 60
CRAWL_LOCK = 'crawl-in-progress'


@shared_task(time_limit=CRAWL_TIME_LIMIT)
def run_crawlers():
    """
    Periodic task to trigger all active web crawlers based on database configuration.

    Only one runs at a time. Every scraper checks whether a URL is already
    held and then inserts it, so two crawls side by side - a long night
    running into the next, or one started by hand - each imported the same
    posts and every work arrived twice.
    """
    token = uuid.uuid4().hex
    try:
        acquired = cache.add(CRAWL_LOCK, token, CRAWL_TIME_LIMIT)
    except Exception:
        # Without the cache there is no lock to take. The broker lives in the
        # same Redis, so a task that got here can almost certainly reach it;
        # if not, crawling unguarded is what happened before.
        acquired = True
    if not acquired:
        print("Another crawl is still running; not starting a second.")
        return
    try:
        _run_crawlers()
    finally:
        try:
            # Only our own lock: one that expired and was taken by a later
            # run is that run's to release.
            if cache.get(CRAWL_LOCK) == token:
                cache.delete(CRAWL_LOCK)
        except Exception:
            pass


def _run_crawlers():
    print("Starting crawler task...")
    active_websites = SourceWebsite.objects.filter(is_active=True)

    scrapers = {
        'mynaatbook': scrape_mynaatbook,
        'desertechoblog': scrape_desertechoblog,
        'damas': scrape_damas,
        'midhah': scrape_midhah,
        'generic': scrape_generic,
        'wayback': scrape_wayback,
        'wordpress_api': scrape_wordpress_api,
    }

    for website in active_websites:
        scraper = scrapers.get(website.parser_type)
        if scraper is None:
            print(f"Unknown parser type '{website.parser_type}' for {website.name}")
            continue
        try:
            scraper(website)
        except BotChallenge as e:
            # Not something a crawler can or should work around.
            print(f"Skipping {website.name}: {e}")
        except RateLimited as e:
            # The host refused for the whole backoff schedule. Leave it for the
            # next run rather than abandoning the remaining sources.
            print(f"Giving up on {website.name} for this run: {e}")
        except Exception as e:
            print(f"{website.name} failed ({type(e).__name__}): {e}")

    # Sources overlap heavily - the same poem is published by several of these
    # sites - so the pass that brings new work in is also the moment new
    # duplicates appear. Filing them here means they are waiting in the admin
    # rather than being found by a reader. Nothing is merged: each pair is left
    # for a person, and a pair already ruled on is not raised again.
    from .dedup import scan

    try:
        filed = scan()
        print(f"Duplicate scan filed {filed} new pair(s) for review.")
    except Exception as e:
        # A failed scan must not lose a successful crawl.
        print(f"Duplicate scan failed ({type(e).__name__}): {e}")

    print("Crawler task finished.")
