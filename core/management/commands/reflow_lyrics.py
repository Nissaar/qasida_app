"""
Add line breaks back to lyrics that were extracted onto a single line.

Some pages give no per-line markup, so extraction collapses the whole poem into
one run of text - the worst case here held 34,000 characters on one line. The
words are intact; only the breaks are missing. Where an original and its
translation alternate, the writing system changes at each boundary, which is
where the breaks belong.

Nothing is accepted unless every non-space character survives, so this can
only ever add whitespace.
"""

import re

from django.core.management.base import BaseCommand

from core.models import Qasida
from core.tasks import MEGA_LINE_CHARS, reflow_run_together

NON_SPACE_RE = re.compile(r'\S')


def _fingerprint(text):
    """Every non-space character, so only whitespace changes can pass."""
    return ''.join(NON_SPACE_RE.findall(text or ''))


class Command(BaseCommand):
    help = "Restore line breaks in lyrics that were extracted as one long line."

    def add_arguments(self, parser):
        parser.add_argument('--ids', type=str, default='')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--threshold', type=int, default=MEGA_LINE_CHARS,
                            help="Treat a line longer than this as collapsed.")

    def handle(self, *args, **options):
        rows = self._targets(options)
        self.stdout.write(f"{len(rows)} row(s) with a line over "
                          f"{options['threshold']} characters.")

        repaired = refused = 0
        for qasida in rows:
            before = qasida.lyrics
            after = reflow_run_together(before, limit=options['threshold'])

            if _fingerprint(after) != _fingerprint(before):
                refused += 1
                self.stdout.write(self.style.WARNING(
                    f"  {qasida.pk} refused: the text would change, not just its breaks"))
                continue

            lines_before = len([l for l in before.splitlines() if l.strip()])
            lines_after = len([l for l in after.splitlines() if l.strip()])
            longest_after = max((len(l) for l in after.splitlines() if l.strip()), default=0)
            if lines_after <= lines_before:
                refused += 1
                self.stdout.write(f"  {qasida.pk} nothing to split")
                continue

            note = (f"  {qasida.pk} lines {lines_before} -> {lines_after}, "
                    f"longest now {longest_after}")
            if options['dry_run']:
                self.stdout.write(note + "  (dry run)")
                continue

            qasida.lyrics = after
            qasida.save(update_fields=['lyrics'])
            repaired += 1
            self.stdout.write(self.style.SUCCESS(note))

        if not options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f"done: {repaired} repaired, {refused} left alone."))

    def _targets(self, options):
        if options['ids']:
            ids = [int(i) for i in options['ids'].split(',') if i.strip()]
            return list(Qasida.objects.filter(pk__in=ids))
        rows = [q for q in Qasida.objects.exclude(lyrics='').only('id', 'lyrics')
                if any(len(l.strip()) > options['threshold']
                       for l in q.lyrics.splitlines())]
        return rows[:options['limit']] if options['limit'] else rows
