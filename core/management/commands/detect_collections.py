"""
Group works that are parts of one larger poem.

Sources list each chapter of a work like the Burdah as its own entry, with the
part number in the title. This finds those runs, creates a Collection for each
and records which part every entry is.

Only runs of two or more parts are grouped, and an entry already assigned to a
collection is left alone unless --force is given.
"""

import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from core.models import Collection, Qasida

# "Burdah Chapter 3", "Burda Ch. 3", "Something Part 2", and a bare trailing
# number as a last resort.
PART_PATTERNS = [
    re.compile(r'^(?P<base>.+?)[\s,:-]+(?:chapter|ch\.?|part|pt\.?|section)\s*(?P<n>\d+)\s*$', re.I),
    re.compile(r'^(?P<base>.+?)\s+(?P<n>\d+)\s*$'),
]

MIN_PARTS = 2
MIN_BASE_LENGTH = 4


def _part_of(title):
    """Return (base name, part number) when a title names a part, else None."""
    title = (title or '').strip()
    for pattern in PART_PATTERNS:
        match = pattern.match(title)
        if not match:
            continue
        base = match.group('base').strip(' -–—:,')
        if len(base) >= MIN_BASE_LENGTH:
            return base, int(match.group('n'))
    return None


class Command(BaseCommand):
    help = "Group multi-part works, such as the chapters of the Burdah, into collections."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--force', action='store_true',
                            help="Reassign works that already belong to a collection.")

    def handle(self, *args, **options):
        rows = Qasida.objects.exclude(title='')
        if not options['force']:
            rows = rows.filter(collection__isnull=True)

        runs = defaultdict(list)
        for qasida in rows.only('id', 'title'):
            found = _part_of(qasida.title)
            if found:
                base, number = found
                runs[base.lower()].append((number, qasida.pk, base))

        groups = {key: parts for key, parts in runs.items() if len(parts) >= MIN_PARTS}
        self.stdout.write(f"{len(groups)} collection(s) found, "
                          f"covering {sum(len(p) for p in groups.values())} works.")

        created = assigned = 0
        for parts in groups.values():
            # Use the spelling of the first part as the collection's name.
            display = sorted(parts)[0][2]
            numbers = sorted(number for number, _, _ in parts)

            if options['dry_run']:
                self.stdout.write(f"  {display[:46]:48} {len(parts)} parts {numbers[:10]}")
                continue

            collection, was_created = Collection.objects.get_or_create(
                name=display, defaults={'slug': slugify(display)[:220]})
            created += was_created
            for number, pk, _ in parts:
                Qasida.objects.filter(pk=pk).update(
                    collection=collection, collection_position=number)
                assigned += 1
            self.stdout.write(self.style.SUCCESS(
                f"  {display[:46]:48} {len(parts)} parts"))

        if not options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f"done: {created} collection(s) created, {assigned} works assigned."))
