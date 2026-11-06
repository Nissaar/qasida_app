"""
The admin's OCR bench: upload a PDF or a screenshot, get the text back.

Reuses the same pipeline the crawlers use for scanned pages - upscale, sharpen,
Tesseract, then drop the Latin/digit debris that surrounds Arabic script - so
what an editor sees here matches what the automated passes produce.

When GEMINI_API_KEY is set the editor can pick Gemini instead. It runs only
when chosen here or with the button on a qasida's form, never in a crawl, and
on the worker, since a many-page PDF takes longer than a web request may.
"""

import io

import pymupdf
from django import forms
from PIL import Image

from .textrepair import (MAX_PDF_PAGES, OCR_DPI, SCAN_OCR_CONFIG, keep_arabic_lines,
                         prepare_scan)

# Language packs installed in the image.
LANGUAGE_CHOICES = [
    ('ara+eng', 'Arabic (with English)'),
    ('urd+eng', 'Urdu (with English)'),
    ('fas+eng', 'Persian (with English)'),
    ('ara', 'Arabic only'),
    ('urd', 'Urdu only'),
    ('eng', 'English only'),
]

# The language name Gemini is told the page is in, for each choice above.
LANGUAGE_NAMES = {'ara': 'Arabic', 'urd': 'Urdu', 'fas': 'Persian', 'eng': 'English'}

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

TESSERACT = 'tesseract'

# What a page sent to Gemini may weigh. A request carries at most 20MB, and a
# phone photo is often larger than the model needs to read it.
GEMINI_PDF_DPI = 200
GEMINI_MAX_SIDE = 3072
GEMINI_MAX_PAGE_BYTES = 6 * 1024 * 1024
GEMINI_FORMATS = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}


def engine_choices():
    """Tesseract, then Gemini (automatic) and each configured model when there are several."""
    from . import gemini

    choices = [(TESSERACT, 'Tesseract (local)')]
    if gemini.enabled():
        names = gemini.models()
        automatic = f"Gemini ({' -> '.join(names)})" if len(names) > 1 else gemini.engine_label()
        choices.append(('gemini', automatic))
        if len(names) > 1:
            choices += [(f'gemini:{name}', f'Gemini: {name} only') for name in names]
    return choices


class OcrUploadForm(forms.Form):
    upload = forms.FileField(
        label='PDF or image',
        help_text='PDF, JPG, PNG or WEBP. Up to 25MB.',
    )
    engine = forms.ChoiceField(
        label='Read with', initial=TESSERACT, choices=[(TESSERACT, 'Tesseract (local)')],
        help_text='Gemini reads harder pages (handwriting, vocalised script, photos) and is billed per page; '
                  'it is only used when picked here.',
    )
    language = forms.ChoiceField(choices=LANGUAGE_CHOICES, initial='ara+eng')
    keep_script_only = forms.BooleanField(
        required=False, initial=True, label='Drop non-Arabic debris',
        help_text='Removes the stray Latin and digits Tesseract leaves around Arabic '
                  'script. Untick when reading an English page. Tesseract only.',
    )
    page_segmentation = forms.ChoiceField(
        label='Layout',
        initial='--psm 4',
        choices=[('--psm 4', 'Columns of text (default)'),
                 ('--psm 6', 'One uniform block'),
                 ('--psm 3', 'Let Tesseract decide')],
        help_text='Tesseract only.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = engine_choices()
        if len(choices) == 1:
            del self.fields['engine']  # no Gemini key: nothing to choose
        else:
            self.fields['engine'].choices = choices

    def gemini_model(self):
        """None for Tesseract; '' for Gemini on its automatic model order; else the one model picked."""
        engine = self.cleaned_data.get('engine', TESSERACT)
        if engine == TESSERACT:
            return None
        return engine.partition(':')[2]

    def language_name(self):
        return LANGUAGE_NAMES.get(self.cleaned_data['language'].split('+')[0], '')

    def clean_upload(self):
        upload = self.cleaned_data['upload']
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError('That file is larger than 25MB.')
        return upload


def _images_from_upload(upload):
    """Yield PIL images: a PDF is rasterised page by page, an image used as-is."""
    data = upload.read()
    if data[:5] == b'%PDF-':
        document = pymupdf.open(stream=data, filetype='pdf')
        try:
            for index in range(min(document.page_count, MAX_PDF_PAGES)):
                pixmap = document[index].get_pixmap(dpi=OCR_DPI)
                yield Image.open(io.BytesIO(pixmap.tobytes('png')))
        finally:
            document.close()
        return
    yield Image.open(io.BytesIO(data))


def gemini_pages(data):
    """-> [(mime type, bytes)] ready to send to Gemini: a PDF's pages as PNG, an image as-is when it can go."""
    if data[:5] == b'%PDF-':
        from .textrepair import pdf_page_pngs
        return [('image/png', png) for _, png in pdf_page_pngs(data, dpi=GEMINI_PDF_DPI, limit=MAX_PDF_PAGES)]
    with Image.open(io.BytesIO(data)) as image:
        mime = GEMINI_FORMATS.get(image.format)
        if mime and len(data) <= GEMINI_MAX_PAGE_BYTES and max(image.size) <= GEMINI_MAX_SIDE:
            return [(mime, data)]
        # Too big, or a format Gemini does not take (GIF, TIFF, BMP): re-encoded as a JPEG of a sensible size.
        page = image.convert('RGB')
        page.thumbnail((GEMINI_MAX_SIDE, GEMINI_MAX_SIDE))
        out = io.BytesIO()
        page.save(out, format='JPEG', quality=90)
        return [('image/jpeg', out.getvalue())]


def gemini_notes(pages):
    """What the page shows under the result: characters read per page, as for Tesseract."""
    notes = [f"page {number}: {len(text)} characters" for number, text in enumerate(pages, start=1)]
    if not any(pages):
        notes.append('Nothing legible was found.')
    return notes


def run_ocr(upload, language, keep_script_only, page_segmentation=SCAN_OCR_CONFIG):
    """Return (text, notes). Notes describe what was read, for the page to show."""
    import pytesseract

    pages, notes = [], []
    for number, image in enumerate(_images_from_upload(upload), start=1):
        with image:
            prepared = prepare_scan(image)
            raw = pytesseract.image_to_string(
                prepared, lang=language, config=page_segmentation)
        cleaned = keep_arabic_lines(raw) if keep_script_only else raw.strip()
        notes.append(f"page {number}: {len(cleaned)} characters")
        if cleaned:
            pages.append(cleaned)

    if not pages:
        notes.append('Nothing legible was found.')
    return "\n\n".join(pages).strip(), notes
