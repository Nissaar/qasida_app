"""
Remove typeset-page apparatus from extracted verse.

Text taken from a scholarly edition arrives with page numbers, verse numbers
and footnote markers interleaved between the lines, which makes the qasida
page unreadable. This strips those lines where they dominate, and refuses the
change if the Arabic would not survive it.
"""

from django.core.management.base import BaseCommand

from core.models import Qasida
from core.tasks import (
    FURNITURE_MIN_KEPT,
    FURNITURE_THRESHOLD,
    _arabic_len,
    furniture_ratio,
    strip_page_furniture,
)


class Command(BaseCommand):
    help = "Strip page numbers and other apparatus out of extracted lyrics."

    def add_arguments(self, parser):
        parser.add_argument('--ids', type=str, default='')
        parser.add_argument('--threshold', type=float, default=FURNITURE_THRESHOLD)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if options['ids']:
            rows = list(Qasida.objects.filter(
                pk__in=[int(i) for i in options['ids'].split(',') if i.strip()]))
        else:
            rows = [q for q in Qasida.objects.exclude(lyrics='')
                    if furniture_ratio(q.lyrics) >= options['threshold']]

        self.stdout.write(f"{len(rows)} row(s) above the furniture threshold.")
        cleaned = refused = 0

        for qasida in rows:
            before_ratio = furniture_ratio(qasida.lyrics)
            tidied = strip_page_furniture(qasida.lyrics)
            kept_arabic = _arabic_len(tidied)
            original_arabic = _arabic_len(qasida.lyrics)

            safe = (original_arabic == 0
                    or kept_arabic >= original_arabic * FURNITURE_MIN_KEPT)
            lines_before = len([l for l in qasida.lyrics.splitlines() if l.strip()])
            lines_after = len([l for l in tidied.splitlines() if l.strip()])

            if not safe or not tidied:
                refused += 1
                self.stdout.write(self.style.WARNING(
                    f"  {qasida.pk} refused: would keep only "
                    f"{kept_arabic}/{original_arabic} Arabic letters"))
                continue

            note = (f"  {qasida.pk} furniture {before_ratio:.0%} -> "
                    f"{furniture_ratio(tidied):.0%}, lines {lines_before} -> {lines_after}, "
                    f"arabic {original_arabic} -> {kept_arabic}")

            if options['dry_run']:
                self.stdout.write(note + "  (dry run)")
                continue

            qasida.lyrics = tidied
            qasida.save(update_fields=['lyrics'])
            cleaned += 1
            self.stdout.write(self.style.SUCCESS(note))

        if not options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f"done: {cleaned} cleaned, {refused} refused as unsafe."))
