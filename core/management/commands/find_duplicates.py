"""
Look through the library for the same poem held twice.

Safe to re-run: a pair already ruled on is left alone, so an editor's decision
survives every later scan. Nothing is merged, hidden or deleted - the command
only files pairs for review, which is what the admin's Duplicate links section
then shows.
"""

from django.core.management.base import BaseCommand

from core import dedup
from core.models import DuplicateLink, Qasida


class Command(BaseCommand):
    help = "Find works that look like copies of each other and file them for review."

    def add_arguments(self, parser):
        parser.add_argument(
            '--threshold', type=float, default=dedup.FUZZY_THRESHOLD,
            help=("How alike two openings must be, 0 to 1 "
                  f"(default {dedup.FUZZY_THRESHOLD}). Lower catches more and "
                  "raises more false pairs."))
        parser.add_argument(
            '--rebuild-signatures', action='store_true',
            help=("Recompute every work's signature first. Needed after the "
                  "signature rules change, not for an ordinary scan."))

    def handle(self, *args, **options):
        if options['rebuild_signatures']:
            self.stdout.write('Rebuilding signatures...')
            rebuilt = 0
            for work in Qasida.objects.only(
                    'id', 'lyrics', 'transliteration').iterator():
                signature = dedup.build_signature(work.lyrics, work.transliteration)
                if signature != work.dedup_signature:
                    work.dedup_signature = signature
                    # update_fields, so this does not rewrite the whole row or
                    # disturb anything an editor has changed meanwhile.
                    Qasida.objects.filter(pk=work.pk).update(
                        dedup_signature=signature)
                    rebuilt += 1
            self.stdout.write(f'  {rebuilt} signatures changed.')

        scanned = Qasida.objects.exclude(dedup_signature='').count()
        self.stdout.write(f'Scanning {scanned} works at threshold '
                          f'{options["threshold"]}...')
        created = dedup.scan(threshold=options['threshold'])

        waiting = DuplicateLink.objects.filter(
            state=DuplicateLink.STATE_PENDING).count()
        self.stdout.write(self.style.SUCCESS(
            f'{created} new pair(s) filed; {waiting} now waiting on a ruling.'))
