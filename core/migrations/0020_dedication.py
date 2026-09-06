"""Turn the free-text dedication into a table of its own."""

import django.db.models.deletion
from django.db import migrations, models


def text_to_rows(apps, schema_editor):
    """
    Carry any dedication already typed in over to the new table.

    Names are matched case-insensitively while moving, so a field that was
    filled in by hand does not arrive as three spellings of one person.
    """
    Qasida = apps.get_model('core', 'Qasida')
    Dedication = apps.get_model('core', 'Dedication')

    seen = {}
    for qasida in Qasida.objects.exclude(dedicated_to='').iterator():
        name = (qasida.dedicated_to or '').strip()
        if not name:
            continue
        key = name.casefold()
        if key not in seen:
            seen[key] = Dedication.objects.get_or_create(name=name)[0]
        qasida.dedication = seen[key]
        qasida.save(update_fields=['dedication'])


def rows_to_text(apps, schema_editor):
    """Put the names back as text, so this migration can be reversed."""
    Qasida = apps.get_model('core', 'Qasida')
    for qasida in Qasida.objects.exclude(dedication=None).select_related('dedication'):
        qasida.dedicated_to = qasida.dedication.name
        qasida.save(update_fields=['dedicated_to'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_qasida_dedicated_to'),
    ]

    operations = [
        migrations.CreateModel(
            name='Dedication',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, unique=True)),
                ('arabic_name', models.CharField(
                    blank=True, max_length=200,
                    help_text='The same name in Arabic script, where there is one.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ('name',)},
        ),
        # Added under a temporary name so the text column can be read while
        # the rows are moved across, then renamed into its place.
        migrations.AddField(
            model_name='qasida',
            name='dedication',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='qasidas', to='core.dedication'),
        ),
        migrations.RunPython(text_to_rows, rows_to_text),
        migrations.RemoveField(model_name='qasida', name='dedicated_to'),
        migrations.RenameField(model_name='qasida', old_name='dedication',
                               new_name='dedicated_to'),
        migrations.AlterField(
            model_name='qasida',
            name='dedicated_to',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='qasidas', to='core.dedication',
                help_text='Who the qasida is addressed to or written in praise '
                          'of. Choose one, or add a new one with the + button.'),
        ),
    ]
