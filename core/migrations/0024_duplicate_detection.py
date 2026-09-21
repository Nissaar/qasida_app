"""Recognising the same poem arriving from more than one source.

Additive. A signature column on the works, a trigram index over it, and a
table of suspected pairs. No existing row is altered beyond having its
signature filled in, and nothing is merged or hidden by this migration.
"""

import django.contrib.postgres.indexes
import django.db.models.deletion
from django.db import migrations, models

from core.dedup import build_signature

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
