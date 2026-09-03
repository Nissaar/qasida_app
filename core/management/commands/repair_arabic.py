"""
Re-read qasidas whose stored Arabic is shattered glyph soup.

Several damas PDFs position each glyph individually and pad with kashida, which
defeats text-layer extraction: the result looks like text but reads as isolated
letters. This command finds those rows, rasterises the source PDF (the pages are
the trustworthy record), tries OCR, and keeps whichever text is better.
"""

import re
import time

import requests
from django.core.management.base import BaseCommand

from core.models import Qasida
from core.tasks import (
    HEADERS,
    PDF_URL_RE,
    _fetch_pdf_text,
    _looks_shattered,
    _ocr_is_improvement,
    _ocr_pdf,
    _reassemble_pdf_text,
    _reassembly_is_improvement,
    _pick_arabic_pdf,
    _store_page_images,
    _text_shatter_score,
    UNRELIABLE_TEXT_TAG,
    _add_tags,
)

DAMAS_API = 'https://damas.nur.nu/wp-json/wp/v2/qasida'


class Command(BaseCommand):
    help = "Rasterise and OCR qasidas whose extracted Arabic text is unusable."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0,
                            help="Only process this many rows (0 = all).")
        parser.add_argument('--ids', type=str, default='',
                            help="Comma-separated qasida ids to process instead of scanning.")
        parser.add_argument('--dry-run', action='store_true',
                            help="Report what would change without writing.")
        parser.add_argument('--force', action='store_true',
                            help="Reprocess rows already marked as OCR'd or unreliable.")

    def handle(self, *args, **options):
        if options['ids']:
            targets = list(Qasida.objects.filter(
                pk__in=[int(i) for i in options['ids'].split(',') if i.strip()]))
        else:
            candidates = Qasida.objects.filter(source_url__contains='damas.nur.nu')
            if not options['force']:
                # Rows flagged 'poor' keep their unreadable text on purpose and
                # rely on the stored page images, so they still look shattered.
                # Skip them or every run would redo the same work.
                candidates = candidates.filter(text_quality=Qasida.TEXT_OK)
            targets = [q for q in candidates if _looks_shattered(q.lyrics)]
        if options['limit']:
            targets = targets[:options['limit']]

        self.stdout.write(f"{len(targets)} qasida(s) to repair.")
        stats = {'ocr': 0, 'reflow': 0, 'poor': 0, 'no_pdf': 0, 'errors': 0, 'pages': 0, 'unchanged': 0}

        for qasida in targets:
            before = _text_shatter_score(qasida.lyrics)[0]
            pdf_url = self._find_pdf(qasida)
            if not pdf_url:
                stats['no_pdf'] += 1
                self.stdout.write(f"  no PDF: {qasida.pk} {qasida.title[:52]}")
                continue

            try:
                pdf_bytes, _ = _fetch_pdf_text(pdf_url)
                if not pdf_bytes:
                    stats['no_pdf'] += 1
                    continue
                # Rebuilding from glyph coordinates is lossless and beats OCR
                # on these layouts, so it is tried first.
                transcript = _reassemble_pdf_text(pdf_bytes)
                method = 'reflow'
                if not _reassembly_is_improvement(transcript, qasida.lyrics):
                    transcript = _ocr_pdf(pdf_bytes)
                    method = 'ocr'
            except Exception as e:
                stats['errors'] += 1
                self.stdout.write(self.style.WARNING(
                    f"  failed {qasida.pk} ({type(e).__name__}): {qasida.title[:44]}"))
                continue

            after = _text_shatter_score(transcript)[0]
            improved = (_reassembly_is_improvement(transcript, qasida.lyrics)
                        if method == 'reflow'
                        else _ocr_is_improvement(transcript, qasida.lyrics))

            if options['dry_run']:
                tokens_before = _text_shatter_score(qasida.lyrics)[2]
                tokens_after = _text_shatter_score(transcript)[2]
                self.stdout.write(
                    f"  {qasida.pk} single% {before:.0%}->{after:.0%} "
                    f"arabic-tokens {tokens_before}->{tokens_after} "
                    f"{('USE ' + method.upper()) if improved else 'flag poor, keep pages'}")
                continue

            if improved:
                qasida.lyrics = transcript
                qasida.text_quality = Qasida.TEXT_OCR
                stats[method] += 1
            else:
                qasida.text_quality = Qasida.TEXT_POOR
                stats['poor'] += 1
            qasida.save(update_fields=['lyrics', 'text_quality'])
            _add_tags(qasida, [UNRELIABLE_TEXT_TAG])

            pages = _store_page_images(qasida, pdf_bytes)
            stats['pages'] += pages
            self.stdout.write(f"  {qasida.pk} single% {before:.0%} -> {after:.0%}, "
                              f"{qasida.text_quality}, +{pages} page image(s): {qasida.title[:40]}")
            time.sleep(1)

        self.stdout.write(self.style.SUCCESS(
            f"done: {stats['reflow']} rebuilt from glyph positions, {stats['ocr']} replaced with OCR, "
            f"{stats['poor']} flagged unreliable, "
            f"{stats['pages']} page images stored, {stats['no_pdf']} without a PDF, "
            f"{stats['errors']} errors."))

    def _find_pdf(self, qasida):
        """Look the post up again by slug to recover its PDF link."""
        slug = (qasida.source_url or '').rstrip('/').rsplit('/', 1)[-1]
        if not slug:
            return None
        try:
            res = requests.get(DAMAS_API, headers=HEADERS, timeout=60, params={'slug': slug})
            posts = res.json()
        except Exception:
            return None
        if not isinstance(posts, list) or not posts:
            return None
        body = posts[0].get('content', {}).get('rendered', '') or ''
        return _pick_arabic_pdf(list(dict.fromkeys(PDF_URL_RE.findall(body))))
