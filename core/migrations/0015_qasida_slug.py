"""
Give every qasida a URL slug.

Done in three steps in one migration: the column cannot be created unique,
because every existing row would receive the same empty value and collide.
It is added plain, filled in, and only then made unique.
"""

from django.db import migrations, models
from django.utils.text import slugify


def populate_slugs(apps, schema_editor):
    Qasida = apps.get_model('core', 'Qasida')

    taken = set()
    # Historical models have no custom methods, so the rule is repeated here.
    for pk, title, transliteration in Qasida.objects.values_list(
            'id', 'title', 'transliteration').iterator(chunk_size=500):
        base = slugify(title or '')
        if not base and transliteration:
            first_line = next(
                (line for line in transliteration.splitlines() if line.strip()), '')
            base = slugify(first_line)
        # A title written only in Arabic or Urdu script slugifies to nothing.
        if not base:
            base = f'qasida-{pk}'
        base = base[:200]

        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f'{base}-{suffix}'[:220]
            suffix += 1
        taken.add(candidate)
        Qasida.objects.filter(pk=pk).update(slug=candidate)


def clear_slugs(apps, schema_editor):
    apps.get_model('core', 'Qasida').objects.update(slug='')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_collection_qasida_collection_position_and_more'),
    ]

    operations = [
        # db_index=False on the way in: a SlugField is indexed by default, and
        # the unique index added below would then try to create the same
        # varchar_pattern_ops index a second time.
        migrations.AddField(
            model_name='qasida',
            name='slug',
            field=models.SlugField(blank=True, db_index=False, default='', max_length=220),
            preserve_default=False,
        ),
        migrations.RunPython(populate_slugs, clear_slugs),
        migrations.AlterField(
            model_name='qasida',
            name='slug',
            field=models.SlugField(blank=True, max_length=220, unique=True),
        ),
    ]
