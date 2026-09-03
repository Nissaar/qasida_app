"""
Populate the parsed title fields and the folded search column.

Safe to re-run: it recomputes from the current row contents, so running it
again after a crawl or a repair pass simply refreshes what changed.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Qasida
from core.search import build_document
from core.titles import split_title


class Command(BaseCommand):
    help = "Split packed titles into title/arabic_title/author and rebuild search_text."

    def add_arguments(self, parser):
        parser.add_argument('--titles', action='store_true',
                            help="Also re-split titles (skip to only refresh search_text).")
        parser.add_argument('--batch', type=int, default=200)

    def handle(self, *args, **options):
        split_titles = options['titles']
        changed_titles = 0
        refreshed = 0
        batch = []

        for qasida in Qasida.objects.all().iterator(chunk_size=options['batch']):
            fields = ['search_text']

            if split_titles:
                title, arabic_title, author = split_title(qasida.title)
                # Only rewrite when the parse actually separated something, so a
                # plain title from another source is never damaged.
                if (arabic_title or author) and title:
                    if (qasida.title, qasida.arabic_title, qasida.author) != (title, arabic_title, author):
                        qasida.title = title
                        qasida.arabic_title = arabic_title
                        qasida.author = author or qasida.author
                        fields += ['title', 'arabic_title', 'author']
                        changed_titles += 1

            document = build_document(qasida.title, qasida.arabic_title,
                                      qasida.author, qasida.lyrics)
            if document != qasida.search_text or len(fields) > 1:
                qasida.search_text = document
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
                # save() recomputes search_text itself; update_fields keeps the
                # write narrow so a concurrent repair pass is not clobbered.
                super(Qasida, qasida).save(update_fields=fields)
