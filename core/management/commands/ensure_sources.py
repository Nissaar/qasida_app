"""Reconcile the SourceWebsite table with the defaults declared in code."""

from django.core.management.base import BaseCommand

from core.models import SourceWebsite
from core.sources import DEFAULT_SOURCES, ensure_sources


class Command(BaseCommand):
    help = "Create any missing default crawl sources (safe to run repeatedly)."

    def handle(self, *args, **options):
        created, updated = ensure_sources(SourceWebsite)
        self.stdout.write(
            f"{len(DEFAULT_SOURCES)} defaults declared: "
            f"{len(created)} created, {len(updated)} updated, "
            f"{len(DEFAULT_SOURCES) - len(created) - len(updated)} already correct."
        )
        for name in created:
            self.stdout.write(self.style.SUCCESS(f"  created {name}"))
        for name in updated:
            self.stdout.write(f"  updated {name}")
