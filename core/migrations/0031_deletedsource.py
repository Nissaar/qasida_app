from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0030_geminiusage'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeletedSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_url', models.URLField(max_length=500, unique=True)),
                ('deleted_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
