"""
Building a PDF of a work for the reader to keep or print.

Arabic and Urdu are the whole difficulty here. A PDF has no shaping engine of
its own: whatever is drawn onto the page is final, so the letters have to be
joined into their contextual forms and put in visual right-to-left order
before they are placed. Drawing the text run by run gets this wrong - the
letters come out unjoined, or in the wrong order, or both.

So the page is described as HTML and handed to MuPDF's Story engine, which
lays it out through HarfBuzz: real shaping, real bidi, and mixed Latin and
Arabic in one line handled correctly. That also gives pagination for free,
which matters because some works here run to several hundred lines.

One known limitation: text copied out of the resulting PDF comes back as
Arabic presentation forms rather than the letters as typed, because of how
MuPDF maps the shaped glyphs. The PDF reads and prints correctly; it is not a
good source to copy and paste from.
"""

import io
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.utils.html import escape

from .export import LAYERS, available_layers
from .templatetags.qasida_extras import stanza_rows

# Amiri, a naskh face drawn for setting classical Arabic, and covering the
# Urdu letters (ٹ ڈ ڑ ں ے) that most Arabic fonts leave out. Installed by the
# Dockerfile; the setting exists so a deployment can point at its own.
DEFAULT_FONT = "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Regular.ttf"

ARABIC_SCRIPT_RE = re.compile(r'[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]')

PAGE = "a4"
MARGIN = 56          # points; a shade over 19mm
FOOTER_SPACE = 34


SITE_NAME = "Qasida Library"
# Used when nothing is configured, so a PDF built in development still carries
# an address rather than a blank.
DEFAULT_SITE_HOST = "www.qasidalibrary.com"

# The green the site is set in, as a PDF colour.
BRAND_RGB = (0.016, 0.471, 0.341)
RULE_RGB = (0.85, 0.83, 0.81)
FOOTER_RGB = (0.47, 0.44, 0.42)


def font_path():
    return Path(getattr(settings, 'QASIDA_PDF_FONT', DEFAULT_FONT))


def bold_font_path():
    """
    The bold cut sitting beside the regular one, when the family ships it.

    Found by name rather than configured separately, because it lives in the
    same directory and that directory is already the archive the story reads
    from. None when there is no such file: a deployment pointing at some other
    font should not have a bold weight declared that MuPDF would then have to
    invent.
    """
    regular = font_path()
    if '-Regular' not in regular.name:
        return None
    candidate = regular.with_name(regular.name.replace('-Regular', '-Bold'))
    return candidate if candidate.is_file() else None


def site_base():
    """
    Where this library lives, without a trailing slash.

    Taken from SITE_URL where it is set, so a deployment cannot hand out PDFs
    pointing at somebody else's domain, and falling back to the public address
    rather than printing nothing at all.
    """
    configured = (getattr(settings, 'SITE_URL', '') or '').strip().rstrip('/')
    return configured or f'https://{DEFAULT_SITE_HOST}'


def site_host():
    """Just the host, which is what reads well in a footer."""
    return urlsplit(site_base()).netloc or DEFAULT_SITE_HOST


def _direction(text):
    """
    Which way a line runs.

    Decided from the text rather than from the language field, which is often
    wrong in this library: several hundred rows labelled Arabic actually hold
    a Latin transliteration.
    """
    return 'rtl' if ARABIC_SCRIPT_RE.search(text or '') else 'ltr'


def _block(text, css_class, number=None):
    """
    One block of verse, escaped, with its line breaks kept.

    `number` prefixes the stanza's place in the poem. It is put on the
    rendering rather than on the verse: the original is what the eye goes to
    first and a numeral in front of it interrupts the line, while on the
    translation it gives a reader working between the layers a way to keep
    their place.
    """
    body = escape(text.strip()).replace('\n', '<br/>')
    if number is not None:
        body = f'<span class="num">{number}.</span> {body}'
    return f'<div class="{css_class}" dir="{_direction(text)}">{body}</div>'


def _document_html(qasida, layers):
    """The whole work as one HTML document, in the order the page shows it."""
    wanted = [name for name in ('original', 'latin', 'translation')
              if name in layers and name in available_layers(qasida)]
    if not wanted:
        wanted = ['original']

    parts = ['<div class="titleblock">']
    # A masthead above the title: a page printed and passed on by itself should
    # still say what collection it came out of.
    parts.append(f'<p class="masthead">{escape(SITE_NAME)}</p>')
    parts.append(f'<h1 dir="{_direction(qasida.title)}">{escape(qasida.title or "Untitled")}</h1>')
    if qasida.native_title:
        parts.append(f'<h2 dir="rtl">{escape(qasida.native_title)}</h2>')
    if qasida.author_id:
        poet = qasida.author.name
        parts.append(f'<p class="byline" dir="{_direction(poet)}">{escape(poet)}</p>')

    meta = []
    if qasida.language:
        meta.append(qasida.language)
    if qasida.collection:
        part = f", part {qasida.collection_position}" if qasida.collection_position else ''
        meta.append(f"{qasida.collection.name}{part}")
    if meta:
        parts.append(f'<p class="meta">{escape(" · ".join(meta))}</p>')
    parts.append('</div>')

    rows = stanza_rows(qasida)
    paired = set()
    # No per-layer captions here any more. Repeating "Transliteration" and
    # "Translation" over every stanza of a long poem says the same thing
    # dozens of times, and the three layers are already told apart by how they
    # are set - the verse large, its romanisation smaller beneath, the
    # rendering smaller still and numbered. The captions stay on a layer that
    # could not be paired, below, where there is nothing else to identify it.
    for index, row in enumerate(rows, start=1):
        stanza = []
        for name in wanted:
            key, _heading = LAYERS[name]
            text = row.get(key) or ''
            if not text.strip():
                continue
            paired.add(name)
            stanza.append(_block(
                text,
                'verse' if name == 'original' else name,
                number=index if name == 'translation' else None))
        if stanza:
            parts.append('<div class="stanza">' + ''.join(stanza) + '</div>')

    # A layer laid out differently to the verses cannot be paired stanza by
    # stanza, so it follows whole rather than being dropped.
    for name in wanted:
        if name in paired or name == 'original':
            continue
        whole = qasida.transliteration if name == 'latin' else qasida.translation
        if whole and whole.strip():
            _, heading = LAYERS[name]
            parts.append(f'<h3>{escape(heading or name.title())}</h3>')
            parts.append(_block(whole, name))

    notes = []
    # The library's own address for this work comes first: a printed sheet
    # that has travelled needs a way back to the record it was taken from,
    # where any correction since will have landed.
    notes.append(f'Read online at {escape(site_base() + qasida.get_absolute_url())}')
    if qasida.source_url:
        notes.append(f'Source: {escape(qasida.source_url)}')
    if qasida.translation and qasida.translation_origin == 'machine':
        notes.append('The translation was produced by machine and may be inaccurate.')
    if qasida.text_quality != 'ok':
        notes.append('This text was reconstructed automatically and may contain errors.')
    if notes:
        parts.append('<div class="notes">' + ''.join(f'<p>{n}</p>' for n in notes) + '</div>')

    return '<div class="doc">' + ''.join(parts) + '</div>'


def _stylesheet(family, has_bold=False):
    """
    Page styling.

    Verse is set larger and more openly than prose: vocalised Arabic carries
    marks above and below every letter, and at a normal body size and leading
    they collide.

    The romanisation is set bold to hold its own between the verse above it
    and the rendering below, but only where a real bold cut was found. Asking
    for a weight the family does not have leaves MuPDF to invent one, which
    looks worse than the regular weight it replaces.
    """
    latin_weight = 'font-weight: bold;' if has_bold else ''
    return f"""
    * {{ font-family: {family}; }}
    /* Centred throughout, the way these texts are set in print and the way
       the site shows them: the verse, its romanisation and its rendering
       stacked on one axis so a stanza reads as one thing. */
    .doc {{ font-size: 11px; color: #1c1917; text-align: center; }}
    h1 {{ font-size: 19px; margin: 0 0 2px 0; }}
    h2 {{ font-size: 17px; margin: 0 0 2px 0; font-weight: normal; }}
    h3 {{ font-size: 13px; margin: 16px 0 4px 0; }}
    .masthead {{ font-size: 8px; letter-spacing: 1.2px; text-transform: uppercase;
                 color: #047857; margin: 0 0 6px 0; }}
    .byline {{ font-size: 11px; margin: 2px 0 0 0; }}
    .meta {{ font-size: 9px; color: #57534e; margin: 2px 0 0 0; }}
    .titleblock {{ margin-bottom: 20px; }}
    .stanza {{ margin-bottom: 18px; }}
    .layer {{ font-size: 8px; color: #78716c; margin: 6px 0 1px 0; }}
    .verse {{ font-size: 15px; line-height: 2.0; margin: 0; }}
    .latin {{ font-size: 10.5px; line-height: 1.7; color: #44403c; margin: 5px 0 0 0;
              {latin_weight} }}
    .translation {{ font-size: 10.5px; line-height: 1.7; color: #57534e; margin: 5px 0 0 0; }}
    .num {{ color: #a8a29e; }}
    /* The colophon is reference matter rather than verse, and a centred
       ragged block of URLs is harder to read than a left-aligned one. */
    .notes {{ font-size: 8px; color: #78716c; margin-top: 20px; text-align: left; }}
    .notes p {{ margin: 0 0 2px 0; }}
    """


def build_pdf(qasida, layers, include_scans=True):
    """Render the requested layers of `qasida` as PDF bytes, with its scans."""
    import pymupdf

    path = font_path()
    if path.is_file():
        family = 'qasidafont'
        faces = f'@font-face {{ font-family: {family}; src: url({path.name}); }}'
        bold = bold_font_path()
        if bold:
            faces += (f'@font-face {{ font-family: {family}; '
                      f'src: url({bold.name}); font-weight: bold; }}')
        css = faces + _stylesheet(family, has_bold=bool(bold))
        archive = pymupdf.Archive(str(path.parent))
    else:
        # MuPDF substitutes from its own fallback fonts, which still shape
        # Arabic correctly - it is the typeface that is lost, not the text.
        css = _stylesheet('serif')
        archive = None

    story = pymupdf.Story(html=_document_html(qasida, layers), user_css=css,
                          archive=archive)

    mediabox = pymupdf.paper_rect(PAGE)
    frame = mediabox + (MARGIN, MARGIN, -MARGIN, -(MARGIN + FOOTER_SPACE))

    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    more = True
    # A runaway layout would otherwise spool pages forever.
    for _ in range(400):
        if not more:
            break
        device = writer.begin_page(mediabox)
        more, _ = story.place(frame)
        story.draw(device)
        writer.end_page()
    writer.close()

    doc = pymupdf.open("pdf", buffer.getvalue())
    if include_scans:
        _append_scans(doc, qasida, css, archive)
    _add_footers(doc)
    out = doc.tobytes(deflate=True)
    doc.close()
    return out


def _append_scans(doc, qasida, css, archive):
    """
    Add the scanned pages after the text, one to a page.

    For a good many works here the scan is not an illustration of the text -
    it is the source the text was read off, and where the reading is doubtful
    it is the only reliable record. A file that leaves it behind is missing
    the part worth keeping.

    A scan whose file has gone missing is skipped rather than failing the
    download: the text is still worth having.
    """
    import pymupdf

    scans = list(qasida.images.all())
    total = len(scans)
    for number, scan in enumerate(scans, start=1):
        try:
            path = Path(scan.image.path)
            if not path.is_file():
                continue
        except (ValueError, NotImplementedError):
            # Storage that is not on a local filesystem has no path.
            continue

        page = doc.new_page(width=pymupdf.paper_rect(PAGE).width,
                            height=pymupdf.paper_rect(PAGE).height)
        caption_height = 26
        frame = page.rect + (MARGIN, MARGIN + caption_height,
                             -MARGIN, -(MARGIN + FOOTER_SPACE))
        try:
            page.insert_image(frame, filename=str(path), keep_proportion=True)
        except Exception:
            # An unreadable or truncated file should not take the PDF with it.
            doc.delete_page(page.number)
            continue

        label = escape(scan.caption) or f'Scanned page {number} of {total}'
        page.insert_htmlbox(
            pymupdf.Rect(MARGIN, MARGIN, page.rect.width - MARGIN,
                         MARGIN + caption_height),
            f'<div style="font-size:9px;color:#78716c">{label}</div>',
            css=css, archive=archive)


def _add_footers(doc):
    """
    Stamp a page number and the library's name along the bottom.

    Done as a last pass because the Story engine lays out one flow and knows
    nothing about page furniture, and because the scans are added after it.
    Latin only, so a built-in font will do.
    """
    import pymupdf

    total = doc.page_count
    host = site_host()
    for number, page in enumerate(doc, start=1):
        y = page.rect.height - MARGIN + 12
        # A hairline across the foot, so the text block has a bottom edge
        # rather than the footer floating loose in the margin.
        page.draw_line(pymupdf.Point(MARGIN, y - 9),
                       pymupdf.Point(page.rect.width - MARGIN, y - 9),
                       color=RULE_RGB, width=0.4)
        # The address in the library's own green, on every page: a sheet that
        # gets photocopied or forwarded still says where it came from.
        page.insert_text((MARGIN, y), host, fontname="hebo",
                         fontsize=7, color=BRAND_RGB)
        label = f"{SITE_NAME}  ·  {number} / {total}"
        width = pymupdf.get_text_length(label, fontname="helv", fontsize=7)
        page.insert_text((page.rect.width - MARGIN - width, y), label,
                         fontname="helv", fontsize=7, color=FOOTER_RGB)


def filename_for(qasida, layers):
    """A filename that says which layers are inside."""
    stem = (qasida.slug or f'qasida-{qasida.pk}')[:100]
    extras = [name for name in ('latin', 'translation') if name in layers]
    if extras:
        stem = f"{stem}-with-{'-and-'.join(extras)}"
    return f"{stem}.pdf"
