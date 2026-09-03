"""
The admin's OCR bench: upload a PDF or a screenshot, get the text back.

Reuses the same pipeline the crawlers use for scanned pages - upscale, sharpen,
Tesseract, then drop the Latin/digit debris that surrounds Arabic script - so
what an editor sees here matches what the automated passes produce.
"""

import io

import pymupdf
from django import forms
from PIL import Image

from .tasks import OCR_DPI, SCAN_OCR_CONFIG, _keep_arabic_lines, _prepare_scan

# Language packs installed in the image.
LANGUAGE_CHOICES = [
    ('ara+eng', 'Arabic (with English)'),
    ('urd+eng', 'Urdu (with English)'),
    ('fas+eng', 'Persian (with English)'),
    ('ara', 'Arabic only'),
    ('urd', 'Urdu only'),
    ('eng', 'English only'),
]

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 30


class OcrUploadForm(forms.Form):
    upload = forms.FileField(
        label='PDF or image',
        help_text='PDF, JPG, PNG or WEBP. Up to 25MB.',
    )
    language = forms.ChoiceField(choices=LANGUAGE_CHOICES, initial='ara+eng')
    keep_script_only = forms.BooleanField(
        required=False, initial=True, label='Drop non-Arabic debris',
        help_text='Removes the stray Latin and digits Tesseract leaves around Arabic '
                  'script. Untick when reading an English page.',
    )
    page_segmentation = forms.ChoiceField(
        label='Layout',
        initial='--psm 4',
        choices=[('--psm 4', 'Columns of text (default)'),
                 ('--psm 6', 'One uniform block'),
                 ('--psm 3', 'Let Tesseract decide')],
    )

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


def run_ocr(upload, language, keep_script_only, page_segmentation=SCAN_OCR_CONFIG):
    """Return (text, notes). Notes describe what was read, for the page to show."""
    import pytesseract

    pages, notes = [], []
    for number, image in enumerate(_images_from_upload(upload), start=1):
        with image:
            prepared = _prepare_scan(image)
            raw = pytesseract.image_to_string(
                prepared, lang=language, config=page_segmentation)
        cleaned = _keep_arabic_lines(raw) if keep_script_only else raw.strip()
        notes.append(f"page {number}: {len(cleaned)} characters")
        if cleaned:
            pages.append(cleaned)

    if not pages:
        notes.append('Nothing legible was found.')
    return "\n\n".join(pages).strip(), notes
