"""
Fill in English translations for works that have none.

Runs locally through Argos Translate, so nothing leaves the host. A translation
published by the source is always preferred and is never overwritten: machine
output is only used where no human rendering exists, and is stored with
translation_origin='machine' so the page can say so.
"""

from django.core.management.base import BaseCommand

from core.models import Qasida
from core.translating import (
    available_source_codes,
    code_for_language,
    is_native_script,
    translate_verse,
)


class Command(BaseCommand):
    help = "Machine-translate works that have no translation yet."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--ids', type=str, default='')
        parser.add_argument('--language', type=str, default='',
                            help="Only this language, e.g. Arabic.")
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--redo-machine', action='store_true',
                            help="Also replace translations produced by an earlier run.")

    def handle(self, *args, **options):
        installed = available_source_codes()
        if not installed:
            self.stderr.write(self.style.ERROR(
                "No Argos models are installed, so nothing can be translated. "
                "Rebuild the image, which installs them."))
            return
        self.stdout.write(f"models available for: {sorted(installed)}")

        rows = self._targets(options, installed)
        self.stdout.write(f"{len(rows)} row(s) to translate.")

        counts = {'translated': 0, 'empty': 0, 'no_model': 0, 'transliterated': 0}
        for qasida in rows:
            code = code_for_language(qasida.language)
            if code not in installed:
                counts['no_model'] += 1
                continue

            if not is_native_script(qasida.lyrics):
                # Stored in Latin transliteration, so there is nothing for the
                # model to read.
                counts['transliterated'] += 1
                continue

            if options['dry_run']:
                lines = len([l for l in qasida.lyrics.splitlines() if l.strip()])
                self.stdout.write(f"  {qasida.pk} would translate {lines} lines from {code}")
                continue

            english = translate_verse(qasida.lyrics, code)
            if not english:
                counts['empty'] += 1
                self.stdout.write(f"  {qasida.pk} produced nothing")
                continue

            qasida.translation = english
            qasida.translation_origin = Qasida.TRANSLATION_MACHINE
            qasida.save(update_fields=['translation', 'translation_origin'])
            counts['translated'] += 1
            self.stdout.write(
                f"  {qasida.pk} {code}->en, {len(english)} chars: {qasida.title[:38]}")

        if not options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f"done: {counts['translated']} translated, {counts['empty']} produced nothing, "
                f"{counts['transliterated']} skipped as Latin transliteration, "
                f"{counts['no_model']} skipped for lack of a model."))

    def _targets(self, options, installed):
        if options['ids']:
            ids = [int(i) for i in options['ids'].split(',') if i.strip()]
            return list(Qasida.objects.filter(pk__in=ids))

        rows = Qasida.objects.exclude(lyrics='')
        if options['redo_machine']:
            # Never touch a translation the source published.
            rows = rows.exclude(translation_origin=Qasida.TRANSLATION_SOURCE)
        else:
            rows = rows.filter(translation='')
        if options['language']:
            rows = rows.filter(language__iexact=options['language'])
        rows = rows.order_by('pk')
        return list(rows[:options['limit']] if options['limit'] else rows)
