from django.db import migrations

def create_initial_websites(apps, schema_editor):
    SourceWebsite = apps.get_model('core', 'SourceWebsite')
    SourceWebsite.objects.create(
        name="My Naat Book",
        url="https://www.mynaatbook.com/",
        parser_type="mynaatbook",
        is_active=True
    )
    SourceWebsite.objects.create(
        name="Desert Echo Blog",
        url="https://desertechoblog.wordpress.com/",
        parser_type="desertechoblog",
        is_active=True
    )
    SourceWebsite.objects.create(
        name="Damas Nur Poetry Archive",
        url="https://damas.nur.nu/30536/poetry-archive/",
        parser_type="damas",
        is_active=True
    )

def remove_initial_websites(apps, schema_editor):
    SourceWebsite = apps.get_model('core', 'SourceWebsite')
    SourceWebsite.objects.filter(parser_type__in=['mynaatbook', 'desertechoblog', 'damas']).delete()

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_sourcewebsite'),
    ]

    operations = [
        migrations.RunPython(create_initial_websites, remove_initial_websites),
    ]
