from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_add_multiscript_parser'),
    ]

    operations = [
        migrations.CreateModel(
            name='GeminiUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.DateField()),
                ('model', models.CharField(max_length=80)),
                ('calls', models.PositiveIntegerField(default=0)),
                ('tokens_in', models.PositiveBigIntegerField(default=0)),
                ('tokens_out', models.PositiveBigIntegerField(default=0)),
            ],
        ),
        migrations.AddConstraint(
            model_name='geminiusage',
            constraint=models.UniqueConstraint(fields=('day', 'model'), name='unique_gemini_usage_day_model'),
        ),
    ]
