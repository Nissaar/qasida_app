"""
The sites this project crawls by default.

Kept in code rather than only in the database so a fresh deployment starts with
the full set: `manage.py ensure_sources` (run from the entrypoint) reconciles the
SourceWebsite table with this list. Editing a row in the admin still wins for
`is_active`, so a site can be paused without it being switched back on at every
restart.
"""

DEFAULT_SOURCES = [
    {
        'url': 'https://www.mynaatbook.com/',
        'name': 'My Naat Book',
        'parser_type': 'mynaatbook',
        'is_active': True,
    },
    {
        'url': 'https://desertechoblog.wordpress.com/',
        'name': 'Desert Echo Blog',
        'parser_type': 'desertechoblog',
        'is_active': True,
    },
    {
        'url': 'https://damas.nur.nu/30536/poetry-archive/',
        'name': 'Damas Nur Poetry Archive',
        'parser_type': 'damas',
        'is_active': True,
    },
    {
        'url': 'https://lyrics.midhah.com/',
        'name': 'Midhah Lyrics',
        'parser_type': 'midhah',
        'is_active': True,
    },
    {
        # Sits behind a Vercel challenge that answers every path with a 429,
        # robots.txt included, so the live site cannot be read. Imported from
        # Internet Archive snapshots instead.
        'url': 'https://www.qasidacollection.com',
        'name': 'Qasida Collection (archive)',
        'parser_type': 'wayback',
        'is_active': True,
    },
]


def ensure_sources(model):
    """
    Make sure every default source exists. Returns (created, updated) names.

    Only the descriptive fields are refreshed on an existing row; `is_active` is
    left alone so an operator's decision to pause a site survives a restart.
    """
    created, updated = [], []
    for spec in DEFAULT_SOURCES:
        obj, was_created = model.objects.get_or_create(
            url=spec['url'],
            defaults={
                'name': spec['name'],
                'parser_type': spec['parser_type'],
                'is_active': spec['is_active'],
            },
        )
        if was_created:
            created.append(spec['name'])
            continue
        changes = {}
        if obj.name != spec['name']:
            changes['name'] = spec['name']
        if obj.parser_type != spec['parser_type']:
            changes['parser_type'] = spec['parser_type']
        if changes:
            for field, value in changes.items():
                setattr(obj, field, value)
            obj.save(update_fields=list(changes))
            updated.append(spec['name'])
    return created, updated
