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


def font_path():
    return Path(getattr(settings, 'QASIDA_PDF_FONT', DEFAULT_FONT))


def _direction(text):
    """
    Which way a line runs.

    Decided from the text rather than from the language field, which is often
    wrong in this library: several hundred rows labelled Arabic actually hold
    a Latin transliteration.
    """
    return 'rtl' if ARABIC_SCRIPT_RE.search(text or '') else 'ltr'


def _block(text, css_class):
    """One block of verse, escaped, with its line breaks kept."""
    body = escape(text.strip()).replace('\n', '<br/>')
    return f'<div class="{css_class}" dir="{_direction(text)}">{body}</div>'


def _document_html(qasida, layers):
    """The whole work as one HTML document, in the order the page shows it."""
    wanted = [name for name in ('original', 'latin', 'translation')
              if name in layers and name in available_layers(qasida)]
    if not wanted:
        wanted = ['original']

    parts = ['<div class="titleblock">']
    parts.append(f'<h1 dir="{_direction(qasida.title)}">{escape(qasida.title or "Untitled")}</h1>')
    if qasida.arabic_title:
        parts.append(f'<h2 dir="rtl">{escape(qasida.arabic_title)}</h2>')
    if qasida.author:
        parts.append(f'<p class="byline" dir="{_direction(qasida.author)}">{escape(qasida.author)}</p>')

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
    label_layers = len(wanted) > 1
    for row in rows:
        stanza = []
        for name in wanted:
            key, heading = LAYERS[name]
            text = row.get(key) or ''
            if not text.strip():
                continue
            paired.add(name)
            if heading and label_layers:
                stanza.append(f'<p class="layer">{escape(heading)}</p>')
            stanza.append(_block(text, 'verse' if name == 'original' else name))
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
    if qasida.source_url:
        notes.append(f'Source: {escape(qasida.source_url)}')
    if qasida.translation and qasida.translation_origin == 'machine':
        notes.append('The translation was produced by machine and may be inaccurate.')
    if qasida.text_quality != 'ok':
        notes.append('This text was reconstructed automatically and may contain errors.')
    if notes:
        parts.append('<div class="notes">' + ''.join(f'<p>{n}</p>' for n in notes) + '</div>')

    return '<div class="doc">' + ''.join(parts) + '</div>'


def _stylesheet(family):
    """
    Page styling.

    Verse is set larger and more openly than prose: vocalised Arabic carries
    marks above and below every letter, and at a normal body size and leading
    they collide.
    """
    return f"""
    * {{ font-family: {family}; }}
    .doc {{ font-size: 11px; color: #1c1917; }}
    h1 {{ font-size: 19px; margin: 0 0 2px 0; }}
    h2 {{ font-size: 17px; margin: 0 0 2px 0; font-weight: normal; }}
    h3 {{ font-size: 13px; margin: 14px 0 4px 0; }}
    .byline {{ font-size: 11px; margin: 2px 0 0 0; }}
    .meta {{ font-size: 9px; color: #57534e; margin: 2px 0 0 0; }}
    .titleblock {{ margin-bottom: 16px; }}
    .stanza {{ margin-bottom: 14px; }}
    .layer {{ font-size: 8px; color: #78716c; margin: 6px 0 1px 0; }}
    .verse {{ font-size: 15px; line-height: 2.0; margin: 0; }}
    .latin {{ font-size: 11px; line-height: 1.7; color: #57534e; margin: 0; }}
    .translation {{ font-size: 11px; line-height: 1.7; color: #44403c; margin: 0; }}
    .notes {{ font-size: 8px; color: #78716c; margin-top: 18px; }}
    .notes p {{ margin: 0 0 2px 0; }}
    """


def build_pdf(qasida, layers):
    """Render the requested layers of `qasida` as PDF bytes."""
    import pymupdf

    path = font_path()
    if path.is_file():
        family = 'qasidafont'
        css = (f'@font-face {{ font-family: {family}; src: url({path.name}); }}'
               + _stylesheet(family))
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

    return _add_footers(buffer.getvalue(), qasida)


def _add_footers(pdf_bytes, qasida):
    """
    Stamp a page number and the library's name along the bottom.

    Done as a second pass because the Story engine lays out one flow and knows
    nothing about page furniture. Latin only, so a built-in font will do.
    """
    import pymupdf

    doc = pymupdf.open("pdf", pdf_bytes)
    total = doc.page_count
    for number, page in enumerate(doc, start=1):
        y = page.rect.height - MARGIN + 12
        page.insert_text((MARGIN, y), "Qasida Library", fontname="helv",
                         fontsize=7, color=(0.47, 0.44, 0.42))
        label = f"{number} / {total}"
        width = pymupdf.get_text_length(label, fontname="helv", fontsize=7)
        page.insert_text((page.rect.width - MARGIN - width, y), label,
                         fontname="helv", fontsize=7, color=(0.47, 0.44, 0.42))
    out = doc.tobytes(deflate=True)
    doc.close()
    return out


def filename_for(qasida, layers):
    """A filename that says which layers are inside."""
    stem = (qasida.slug or f'qasida-{qasida.pk}')[:100]
    extras = [name for name in ('latin', 'translation') if name in layers]
    if extras:
        stem = f"{stem}-with-{'-and-'.join(extras)}"
    return f"{stem}.pdf"
