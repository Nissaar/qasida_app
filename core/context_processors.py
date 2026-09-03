"""Shell-wide values. The header, footer and empty states all quote library
totals, so they are supplied globally rather than threaded through every view."""

from django.db.models import Count

from .models import Qasida, QasidaImage, Tag


def library_stats(request):
    """
    Totals for the shell.

    Counted over what this viewer may actually see, so a reader is never told
    the library holds works that the review gate is still hiding.
    """
    visible = Qasida.objects.visible_to(getattr(request, 'user', None))
    return {
        'library_total': visible.count(),
        'library_scans': QasidaImage.objects.filter(qasida__in=visible).count(),
        'library_tags': Tag.objects.filter(qasidas__in=visible).distinct().count(),
        'library_pending': (Qasida.objects.filter(review_state=Qasida.REVIEW_PENDING).count()
                            if getattr(getattr(request, 'user', None), 'is_staff', False) else 0),
    }
