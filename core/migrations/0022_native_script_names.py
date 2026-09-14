"""Rename the script-specific fields to script-neutral ones.

Urdu, Persian and Arabic are all written in the Arabic script, so one column
always served them all - it was the name that was wrong, and this library is
some 3,300 Urdu works to 470 Arabic, so the name described the exception. A
rename only: no column is added, dropped, or rewritten, and every value stays
exactly where it was.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_poet'),
    ]

    operations = [
        migrations.RenameField(model_name='qasida', old_name='arabic_title',
                               new_name='native_title'),
        migrations.RenameField(model_name='suggestion',
                               old_name='suggested_arabic_title',
                               new_name='suggested_native_title'),
        migrations.RenameField(model_name='poet', old_name='arabic_name',
                               new_name='native_name'),
        migrations.RenameField(model_name='dedication', old_name='arabic_name',
                               new_name='native_name'),
        migrations.RenameField(model_name='collection', old_name='arabic_name',
                               new_name='native_name'),
        # The wording changes with the name: the field never required Arabic,
        # it required whatever script the thing is actually written in.
        migrations.AlterField(
            model_name='poet',
            name='native_name',
            field=models.CharField(
                blank=True, max_length=200,
                help_text='The same name in its own script, where there is one.'),
        ),
        migrations.AlterField(
            model_name='dedication',
            name='native_name',
            field=models.CharField(
                blank=True, max_length=200,
                help_text='The same name in its own script, where there is one.'),
        ),
    ]
