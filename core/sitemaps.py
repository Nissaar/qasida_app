"""
What the search engines are told exists.

Only approved works appear. The review gate is not a display preference: a
page the site will not serve must not be advertised either, or a crawler is
sent to collect 404s and learns to trust the sitemap less.

No Site record is configured, so these fall back to the host the request
arrived on, which is what a site behind Traefik with one domain wants.
"""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Collection, Dedication, Poet, Qasida


class QasidaSitemap(Sitemap):
    """The works themselves, which is what anyone is searching for."""

    changefreq = 'monthly'
    limit = 2000

    def items(self):
        return (Qasida.objects.approved()
                .only('slug', 'title', 'created_at')
                .order_by('-created_at'))

    def location(self, obj):
        return obj.get_absolute_url()

    def lastmod(self, obj):
        # reviewed_at is when the text was last vouched for; created_at is all
        # there is for anything approved before that was recorded.
        return obj.reviewed_at or obj.created_at

    def priority(self, obj):
        # A work carrying a translation or a transliteration is more use to a
        # reader arriving from a search than one holding only the original.
        return 0.8 if (obj.translation or obj.transliteration) else 0.6


class PoetSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        return Poet.objects.filter(qasidas__review_state=Qasida.REVIEW_APPROVED).distinct()

    def location(self, obj):
        return reverse('poet', kwargs={'name': obj.name})


class DedicationSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        return Dedication.objects.filter(
            qasidas__review_state=Qasida.REVIEW_APPROVED).distinct()

    def location(self, obj):
        return reverse('dedication', kwargs={'name': obj.name})


class CollectionSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        return Collection.objects.filter(
            parts__review_state=Qasida.REVIEW_APPROVED).distinct()

    def location(self, obj):
        return reverse('collection', kwargs={'slug': obj.slug})


class StaticSitemap(Sitemap):
    """The handful of pages that are not a record of something."""

    changefreq = 'weekly'
    priority = 0.7

    def items(self):
        # The contribution forms are deliberately absent: both need an account,
        # so a crawler sent there collects a sign-in page wearing their titles.
        return ['home', 'browse', 'poets', 'dedications', 'collections',
                'categories', 'about', 'contribute', 'contact', 'privacy']

    def location(self, name):
        return reverse(name)


SITEMAPS = {
    'qasidas': QasidaSitemap,
    'poets': PoetSitemap,
    'dedications': DedicationSitemap,
    'collections': CollectionSitemap,
    'pages': StaticSitemap,
}
