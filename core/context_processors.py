"""Shell-wide values. The header, footer and empty states all quote library
totals, so they are supplied globally rather than threaded through every view."""

from django.conf import settings
from django.core.cache import cache

from .models import Qasida, QasidaImage, Tag

# How long the shell's totals are reused. They change when a crawl lands or an
# editor approves something, and nobody needs the count in the footer to the
# second; counting them afresh on every page - including sw.js, robots.txt
# and every admin page, which render through the same processors - cost
# three or four COUNT queries a request, one of them over a join.
STATS_TTL = 60


def library_stats(request):
    """
    Totals for the shell.

    Counted over what this viewer may actually see, so a reader is never told
    the library holds works that the review gate is still hiding. Staff see
    more, so their totals are kept apart from everyone else's.
    """
    user = getattr(request, 'user', None)
    is_staff = bool(getattr(user, 'is_staff', False))
    key = f'library-stats:{"staff" if is_staff else "reader"}'
    try:
        stats = cache.get(key)
    except Exception:
        stats = None
    if stats is None:
        stats = _count(user, is_staff)
        try:
            cache.set(key, stats, STATS_TTL)
        except Exception:
            pass
    return stats


def _count(user, is_staff):
    visible = Qasida.objects.visible_to(user)
    return {
        'library_total': visible.count(),
        'library_scans': QasidaImage.objects.filter(qasida__in=visible).count(),
        'library_tags': Tag.objects.filter(qasidas__in=visible).distinct().count(),
        'library_pending': (Qasida.objects.filter(review_state=Qasida.REVIEW_PENDING).count()
                            if is_staff else 0),
    }


def site_contact(request):
    """
    The address the library is reachable at.

    In the shell rather than in each view, because the footer prints it on
    every page and the contact and about pages quote the same one. Held in
    settings so it is set once, per deployment, instead of being typed into
    templates where a change means finding them all.
    """
    return {'contact_email': settings.CONTACT_EMAIL}
