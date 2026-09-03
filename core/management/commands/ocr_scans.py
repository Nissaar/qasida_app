"""
Read text off qasidas that exist only as scanned pages.

Some sources publish a photograph of the page and no machine-readable text, so
those rows carry little more than a heading. This runs the scans through OCR
and keeps the result when it recovers materially more Arabic than the row
already holds.
"""

from django.core.management.base import BaseCommand

from core.models import Qasida
from core.tasks import (
    SCAN_OCR_MIN_GAIN,
    UNRELIABLE_TEXT_TAG,
    _add_tags,
    _arabic_len,
    ocr_scanned_images,
)


class Command(BaseCommand):
    help = "OCR the stored scans of qasidas that have no usable text."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0, help="Process at most this many rows.")
        parser.add_argument('--ids', type=str, default='', help="Comma-separated qasida ids.")
        parser.add_argument('--dry-run', action='store_true', help="Report without writing.")
        parser.add_argument('--force', action='store_true',
                            help="Include rows already carrying OCR text.")

    def handle(self, *args, **options):
        targets = self._targets(options)
        self.stdout.write(f"{len(targets)} row(s) to read.")

        counts = {'improved': 0, 'kept_existing': 0, 'nothing_read': 0}
        for qasida in targets:
            before = _arabic_len(qasida.lyrics)
            text = ocr_scanned_images(qasida)
            after = _arabic_len(text)

            if not text:
                counts['nothing_read'] += 1
                self.stdout.write(f"  {qasida.pk} nothing legible: {qasida.title[:44]}")
                continue

            # Require a real gain: OCR that reads less than the row already has
            # would be a regression dressed up as an update.
            better = after >= max(before * SCAN_OCR_MIN_GAIN, before + 20)
            arrow = f"{before} -> {after} arabic letters"

            if options['dry_run']:
                self.stdout.write(f"  {qasida.pk} {arrow} "
                                  f"{'WOULD USE OCR' if better else 'keep existing'}")
                continue

            if not better:
                counts['kept_existing'] += 1
                self.stdout.write(f"  {qasida.pk} {arrow} keeping existing text")
                continue

            qasida.lyrics = text
            qasida.text_quality = Qasida.TEXT_OCR
            qasida.save(update_fields=['lyrics', 'text_quality'])
            _add_tags(qasida, [UNRELIABLE_TEXT_TAG])
            counts['improved'] += 1
            self.stdout.write(self.style.SUCCESS(
                f"  {qasida.pk} {arrow} OCR applied: {qasida.title[:40]}"))

        if not options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f"done: {counts['improved']} replaced with OCR, "
                f"{counts['kept_existing']} left as they were, "
                f"{counts['nothing_read']} with nothing legible."))

    def _targets(self, options):
        if options['ids']:
            ids = [int(i) for i in options['ids'].split(',') if i.strip()]
            return list(Qasida.objects.filter(pk__in=ids))

        rows = Qasida.objects.filter(images__isnull=False).distinct()
        if not options['force']:
            # Rows already holding OCR output are skipped so repeat runs are cheap.
            rows = rows.exclude(text_quality=Qasida.TEXT_OCR)
        rows = [q for q in rows if q.images.exists()]
        if options['limit']:
            rows = rows[:options['limit']]
        return rows
