"""Remove Markdown asterisks from crawled verse and restore its line breaks."""

from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import Qasida
from core.verse_markers import describe, has_markers, normalise

FIELDS = ('lyrics', 'transliteration', 'translation')


class Command(BaseCommand):
    help = ("Strip Markdown emphasis from verse and turn asterisk dividers "
            "into line breaks. Reports what it would do unless --apply is given.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help="Write the changes. Without this nothing is modified.")
        parser.add_argument(
            '--slug', help="Only this one work, for checking a single case.")
        parser.add_argument(
            '--show', type=int, default=5,
            help="How many examples to print (shapes only, not the text).")

    def handle(self, *args, **options):
        rows = Qasida.objects.all()
        if options['slug']:
            rows = rows.filter(slug=options['slug'])
        else:
            rows = rows.filter(Q(lyrics__contains='*') |
                               Q(transliteration__contains='*') |
                               Q(translation__contains='*'))

        changed, shown, per_field = 0, 0, {name: 0 for name in FIELDS}

        for qasida in rows.iterator(chunk_size=200):
            updates = {}
            for name in FIELDS:
                current = getattr(qasida, name) or ''
                if not has_markers(current):
                    continue
                cleaned = normalise(current)
                if cleaned != current:
                    updates[name] = cleaned
                    per_field[name] += 1

            if not updates:
                continue
            changed += 1

            if shown < options['show']:
                shown += 1
                self.stdout.write(f"\n{qasida.slug}")
                for name, cleaned in updates.items():
                    before = getattr(qasida, name)
                    self.stdout.write(
                        f"  {name}: {len(before.splitlines())} line(s) "
                        f"-> {len(cleaned.splitlines())}")
                    # Shapes only: the poetry itself is not reprinted.
                    self.stdout.write(f"    before {describe(before)}")
                    self.stdout.write(f"    after  {describe(cleaned)}")

            if options['apply']:
                for name, cleaned in updates.items():
                    setattr(qasida, name, cleaned)
                qasida.save(update_fields=list(updates))

        self.stdout.write("")
        for name, count in per_field.items():
            self.stdout.write(f"{name:16} {count} row(s) carry markers")
        verb = "cleaned" if options['apply'] else "would be cleaned"
        self.stdout.write(self.style.SUCCESS(f"{changed} qasida(s) {verb}."))
        if not options['apply'] and changed:
            self.stdout.write("Nothing was written. Re-run with --apply to keep it.")
