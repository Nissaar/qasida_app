"""Two tables for what arrives from outside: contributions, and messages.

Both are additive. Nothing existing is altered, so this applies to a running
library without touching a single stored work.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0022_native_script_names'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContactMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('email', models.EmailField(max_length=254)),
                ('topic', models.CharField(
                    choices=[('general', 'General enquiry'),
                             ('correction', 'A mistake in a text'),
                             ('contribute', 'Offering a qasida or a scan'),
                             ('rights', 'Copyright or a request to take something down'),
                             ('technical', 'Something is broken')],
                    default='general', max_length=12)),
                ('message', models.TextField()),
                ('is_handled', models.BooleanField(
                    default=False, help_text='Tick once this has been answered.',
                    verbose_name='Dealt with')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='contact_messages', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ('-created_at',)},
        ),
        migrations.CreateModel(
            name='Contribution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('kind', models.CharField(
                    choices=[('request', 'Request - please add this'),
                             ('submission', 'Submission - here is the text')],
                    default='submission', max_length=10)),
                ('title', models.CharField(blank=True, max_length=200)),
                ('native_title', models.CharField(
                    blank=True,
                    help_text='The title in its own script, where you know it.',
                    max_length=200)),
                ('poet_name', models.CharField(
                    blank=True, help_text='Who wrote it, if you know.',
                    max_length=200, verbose_name='Poet')),
                ('dedication_name', models.CharField(
                    blank=True,
                    help_text='Who it is addressed to or written in praise of.',
                    max_length=200, verbose_name='In praise of')),
                ('language', models.CharField(blank=True, max_length=50)),
                ('lyrics', models.TextField(blank=True)),
                ('transliteration', models.TextField(blank=True)),
                ('translation', models.TextField(blank=True)),
                ('source_url', models.URLField(
                    blank=True,
                    help_text='A link to where this was published, if there is one.',
                    max_length=500)),
                ('source_note', models.TextField(
                    blank=True,
                    help_text='Where it comes from: a book, a recording, a gathering.')),
                ('note', models.TextField(
                    blank=True, help_text='Anything the editors should know.')),
                ('status', models.CharField(
                    choices=[('pending', 'Waiting for an editor'),
                             ('accepted', 'Accepted'),
                             ('declined', 'Declined')],
                    db_index=True, default='pending', max_length=8)),
                ('staff_note', models.TextField(
                    blank=True,
                    help_text='Shown to them on their contributions page, and emailed.',
                    verbose_name='Reply to the contributor')),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('published_as', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='contributions', to='core.qasida')),
                ('reviewed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='contributions_reviewed', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='contributions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('-created_at',),
                'indexes': [models.Index(fields=['status', 'kind'],
                                         name='contribution_queue_idx')],
            },
        ),
    ]
