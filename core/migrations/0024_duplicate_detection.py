"""Recognising the same poem arriving from more than one source.

Additive. A signature column on the works, a trigram index over it, and a
table of suspected pairs. No existing row is altered beyond having its
signature filled in, and nothing is merged or hidden by this migration.
"""

import re
import unicodedata

import django.contrib.postgres.indexes
import django.db.models.deletion
from django.db import migrations, models

# The signature rules, frozen as they stand here, rather than imported from
# core.dedup and core.search. A migration that imports live code computes
# whatever that code says on the day it is applied - and breaks outright if
# it moves - so a fresh install would no longer reproduce this migration.
# Signatures are rebuilt with `find_duplicates --rebuild-signatures` when the
# rules change, not by editing this.
SIGNATURE_LENGTH = 240
DIACRITICS_RE = re.compile('[\u064b-\u0652\u0653-\u0655\u0670\u06d6-\u06ed\u0640]')
LETTER_FOLDING = str.maketrans({
    '\u0623': '\u0627', '\u0625': '\u0627', '\u0622': '\u0627', '\u0671': '\u0627',
    '\u0649': '\u064a', '\u0626': '\u064a',
    '\u0624': '\u0648',
    '\u0629': '\u0647',
    '\ufdf2': '\u0627\u0644\u0644\u0647',
})
PUNCTUATION_RE = re.compile(r'[^\w\s\u0600-\u06ff]+', re.UNICODE)
WHITESPACE_RE = re.compile(r'\s+')


def normalize(text):
    if not text:
        return ''
    text = unicodedata.normalize('NFKC', text)
    text = DIACRITICS_RE.sub('', text)
    text = text.translate(LETTER_FOLDING)
    text = PUNCTUATION_RE.sub(' ', text)
    return WHITESPACE_RE.sub(' ', text).strip().lower()


def build_signature(*texts):
    for text in texts:
        folded = normalize(text)
        if folded:
            return folded[:SIGNATURE_LENGTH]
    return ''

# Large enough to make the backfill a handful of statements rather than one per
# work, small enough not to hold the whole library in memory at once.
BATCH = 500


def fill_signatures(apps, schema_editor):
    """
    Give the works already held a signature.

    Without this the table only recognises duplicates among works crawled after
    the migration, which is the opposite of what it is for: the overlap worth
    finding is in what has already been collected.
    """
    Qasida = apps.get_model('core', 'Qasida')
    batch = []
    for work in Qasida.objects.only('id', 'lyrics', 'transliteration').iterator():
        work.dedup_signature = build_signature(work.lyrics, work.transliteration)
        batch.append(work)
        if len(batch) >= BATCH:
            Qasida.objects.bulk_update(batch, ['dedup_signature'])
            batch = []
    if batch:
        Qasida.objects.bulk_update(batch, ['dedup_signature'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_contribution_contactmessage'),
    ]

    operations = [
        # Validation only - the column is already NOT NULL with '' for a blank
        # text field, so nothing stored changes. It stops the admin demanding
        # an original text for a work that only ever had a romanisation.
        migrations.AlterField(
            model_name='qasida',
            name='lyrics',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='qasida',
            name='dedup_signature',
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddIndex(
            model_name='qasida',
            index=django.contrib.postgres.indexes.GinIndex(
                fields=['dedup_signature'], name='qasida_dedup_trgm',
                opclasses=['gin_trgm_ops']),
        ),
        migrations.CreateModel(
            name='DuplicateLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('score', models.FloatField(
                    default=0,
                    help_text='How alike the two openings are, 0 to 1.')),
                ('matched_on', models.CharField(
                    choices=[('opening', 'Identical opening'),
                             ('fuzzy', 'Similar opening')],
                    default='fuzzy', max_length=8)),
                ('state', models.CharField(
                    choices=[('pending', 'Not yet reviewed'),
                             ('duplicate', 'Confirmed duplicate'),
                             ('distinct', 'Different works')],
                    db_index=True, default='pending', max_length=9)),
                ('note', models.CharField(
                    blank=True, help_text='Why you ruled the way you did.',
                    max_length=280)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('first', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='duplicate_links_as_first', to='core.qasida')),
                ('second', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='duplicate_links_as_second', to='core.qasida')),
            ],
            options={
                'ordering': ('state', '-score', '-created_at'),
            },
        ),
        migrations.AddConstraint(
            model_name='duplicatelink',
            constraint=models.UniqueConstraint(fields=('first', 'second'),
                                               name='unique_duplicate_pair'),
        ),
        migrations.RunPython(fill_signatures, migrations.RunPython.noop),
    ]
