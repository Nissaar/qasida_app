"""
Populate the parsed title fields and the derived search columns.

Safe to re-run: it recomputes from the current row contents, so running it
again after a crawl or a repair pass simply refreshes what changed.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Poet, Qasida
from core.titles import split_title


class Command(BaseCommand):
    help = ("Split packed titles into title/native_title/poet and rebuild "
            "search_text and dedup_signature.")

    def add_arguments(self, parser):
        parser.add_argument('--titles', action='store_true',
                            help="Also re-split titles (skip to only refresh the search columns).")
        parser.add_argument('--batch', type=int, default=200)

    def handle(self, *args, **options):
        split_titles = options['titles']
        changed_titles = 0
        refreshed = 0
        batch = []

        works = Qasida.objects.select_related('author', 'dedicated_to')
        for qasida in works.iterator(chunk_size=options['batch']):
            fields = []

            if split_titles:
                title, native_title, author = split_title(qasida.title)
                current_author = qasida.author.name if qasida.author_id else ''
                # Only rewrite when the parse actually separated something, so a
                # plain title from another source is never damaged.
                if (native_title or author) and title:
                    if (qasida.title, qasida.native_title, current_author) != (
                            title, native_title, author or current_author):
                        qasida.title = title
                        qasida.native_title = native_title
                        if author:
                            qasida.author = Poet.named(author)
                        fields += ['title', 'native_title', 'author']
                        changed_titles += 1

            # The same builders save() uses, so the backfill cannot drop a
            # field that search or duplicate detection has come to rely on.
            stale = (qasida.build_search_text() != qasida.search_text
                     or qasida.build_dedup_signature() != qasida.dedup_signature)
            if stale or fields:
                batch.append((qasida, fields))
                refreshed += 1

            if len(batch) >= options['batch']:
                self._flush(batch)
                batch = []

        self._flush(batch)
        self.stdout.write(self.style.SUCCESS(
            f"done: {changed_titles} titles split, {refreshed} rows refreshed."))

    @staticmethod
    def _flush(batch):
        if not batch:
            return
        with transaction.atomic():
            for qasida, fields in batch:
                # save() recomputes search_text and dedup_signature and adds
                # them to update_fields; keeping the write this narrow means a
                # concurrent repair pass is not clobbered.
                qasida.save(update_fields=fields or ['search_text'])
