"""Turn the free-text author into a table of its own."""

import django.db.models.deletion
from django.db import migrations, models


def text_to_rows(apps, schema_editor):
    """
    Carry every author already recorded over to the new table.

    Names are matched without regard to case while moving, so a field filled
    in by three different crawlers does not arrive as three records of one
    person. Works naming no author - about half of this library - simply
    point at nothing, which is what an empty string always meant.
    """
    Qasida = apps.get_model('core', 'Qasida')
    Poet = apps.get_model('core', 'Poet')

    seen = {}
    for qasida in Qasida.objects.exclude(author='').iterator(chunk_size=500):
        name = (qasida.author or '').strip()
        if not name:
            continue
        key = name.casefold()
        if key not in seen:
            seen[key] = Poet.objects.get_or_create(name=name)[0]
        qasida.poet = seen[key]
        qasida.save(update_fields=['poet'])


def rows_to_text(apps, schema_editor):
    """Put the names back as text, so this migration can be reversed."""
    Qasida = apps.get_model('core', 'Qasida')
    for qasida in (Qasida.objects.exclude(poet=None)
                   .select_related('poet').iterator(chunk_size=500)):
        qasida.author = qasida.poet.name
        qasida.save(update_fields=['author'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_dedication'),
    ]

    operations = [
        migrations.CreateModel(
            name='Poet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, unique=True)),
                ('arabic_name', models.CharField(
                    blank=True, max_length=200,
                    help_text='The same name in Arabic script, where there is one.')),
                ('notes', models.TextField(
                    blank=True,
                    help_text='Anything worth recording: dates, order, region.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ('name',)},
        ),
        # Added under a temporary name so the text column can still be read
        # while the rows are moved across, then renamed into its place.
        migrations.AddField(
            model_name='qasida',
            name='poet',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='qasidas', to='core.poet'),
        ),
        migrations.RunPython(text_to_rows, rows_to_text),
        migrations.RemoveField(model_name='qasida', name='author'),
        migrations.RenameField(model_name='qasida', old_name='poet',
                               new_name='author'),
        migrations.AlterField(
            model_name='qasida',
            name='author',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='qasidas', to='core.poet',
                help_text='Who wrote it. Choose one, or add a new one with the +.'),
        ),
    ]
