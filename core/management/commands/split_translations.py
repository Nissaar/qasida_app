"""
Separate a source-published translation out of the original verse.

Several sources ship the original and their own English rendering interleaved
in one document, so extraction lands both in `lyrics`. Lines are almost always
in one script or the other (measured at ~3% mixed), so the two can be split
apart by script and stored separately.
"""

import re

from django.core.management.base import BaseCommand

from core.models import Qasida

ARABIC_RE = re.compile(r'[؀-ۿ]')
LATIN_RE = re.compile(r'[A-Za-z]')

# Both halves must carry this much text before a row is treated as bilingual.
MIN_SIDE_CHARS = 80
# A line counts as belonging to whichever script dominates it.
DOMINANCE = 2


def split_by_script(text):
    """Return (original, translation) split line by line."""
    original, translation = [], []
    for line in (text or '').splitlines():
        stripped = line.strip()
        if not stripped:
            original.append('')
            translation.append('')
            continue
        arabic = len(ARABIC_RE.findall(stripped))
        latin = len(LATIN_RE.findall(stripped))
        if arabic and arabic >= latin * DOMINANCE:
            original.append(stripped)
        elif latin and latin >= arabic * DOMINANCE:
            translation.append(stripped)
        else:
            # Genuinely mixed: keep it with the original rather than guessing.
            original.append(stripped)

    def tidy(lines):
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()

    return tidy(original), tidy(translation)


class Command(BaseCommand):
    help = "Split interleaved source translations into the translation field."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--ids', type=str, default='')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--force', action='store_true',
                            help="Re-split rows that already have a translation.")

    def handle(self, *args, **options):
        rows = self._targets(options)
        self.stdout.write(f"{len(rows)} candidate row(s).")

        counts = {'split': 0, 'skipped': 0}
        for qasida in rows:
            original, translation = split_by_script(qasida.lyrics)
            if len(original) < MIN_SIDE_CHARS or len(translation) < MIN_SIDE_CHARS:
                counts['skipped'] += 1
                continue

            if options['dry_run']:
                self.stdout.write(
                    f"  {qasida.pk} {len(qasida.lyrics)} chars -> "
                    f"{len(original)} original + {len(translation)} translation")
                continue

            qasida.lyrics = original
            qasida.translation = translation
            qasida.translation_origin = Qasida.TRANSLATION_SOURCE
            qasida.save(update_fields=['lyrics', 'translation', 'translation_origin'])
            counts['split'] += 1

        if not options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f"done: {counts['split']} rows split, {counts['skipped']} left alone."))
        else:
            self.stdout.write(f"(dry run) would split {len(rows) - counts['skipped']}, "
                              f"leave {counts['skipped']}")

    def _targets(self, options):
        if options['ids']:
            ids = [int(i) for i in options['ids'].split(',') if i.strip()]
            return list(Qasida.objects.filter(pk__in=ids))
        rows = Qasida.objects.exclude(lyrics='')
        if not options['force']:
            rows = rows.filter(translation='')
        # Only rows that actually hold both scripts are worth examining.
        return [q for q in rows
                if len(ARABIC_RE.findall(q.lyrics)) > 100
                and len(LATIN_RE.findall(q.lyrics)) > 100][:options['limit'] or None]
