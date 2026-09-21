"""
Recognising the same work arriving from more than one source.

This library is assembled from several sites that each publish their own copy
of a shared repertoire, so the same poem arrives repeatedly: under a different
title, with the poet's name spelled another way, a stanza longer or shorter,
and sometimes in a different script.

Nothing is ever merged automatically. A second copy is often the better one -
fuller, vocalised, carrying a transliteration or a translation someone spent
real time on - so which to keep, and whether they are the same poem at all, is
an editorial judgement. This module only records the suspicion; a person rules
on it.

Two signals, taken together over one bounded slice of the opening:

* the opening compared exactly. In this repertoire the matla identifies the
  poem, while titles are frequently the source site's own invention and vary
  freely between sites.
* trigram similarity over the same opening, which catches the near misses a
  literal comparison loses - a dropped word, a different romanisation of the
  same line, a source that opens with the refrain.
"""

from django.contrib.postgres.search import TrigramSimilarity

from .search import normalize

# How much of the opening to keep. Long enough that two poems sharing a
# formulaic first line are still told apart, short enough that a source
# carrying extra closing stanzas matches one that stops earlier.
SIGNATURE_LENGTH = 240

# Below this, two openings are merely drawing on the same devotional
# vocabulary, which nearly everything here does.
FUZZY_THRESHOLD = 0.55


def build_signature(*texts):
    """
    A comparable form of a work's opening, from the first text that has one.

    `normalize` already folds the diacritics Arabic is stored with but rarely
    typed with, drops punctuation and collapses whitespace, which is exactly
    the variation that separates two sites' copies of one line.
    """
    for text in texts:
        folded = normalize(text)
        if folded:
            return folded[:SIGNATURE_LENGTH]
    return ''


def candidates_for(qasida, threshold=FUZZY_THRESHOLD):
    """
    Works whose opening reads like this one's, closest first.

    Yields (other, score, matched_on). An identical opening scores 1.0 and so
    arrives through the same query as the near misses.
    """
    from .models import DuplicateLink, Qasida

    signature = qasida.dedup_signature
    if not signature:
        return []

    others = (Qasida.objects
              .exclude(pk=qasida.pk)
              .exclude(dedup_signature='')
              .annotate(score=TrigramSimilarity('dedup_signature', signature))
              .filter(score__gte=threshold)
              .order_by('-score'))

    return [(other,
             round(other.score, 3),
             DuplicateLink.MATCH_OPENING if other.dedup_signature == signature
             else DuplicateLink.MATCH_FUZZY)
            for other in others]


def record_duplicates(qasida, threshold=FUZZY_THRESHOLD):
    """
    Link this work to anything that looks like the same poem.

    Returns the number of new links. A pair already ruled on is left alone:
    re-running the scan must not reopen a question an editor has answered.
    """
    from .models import DuplicateLink

    created_count = 0
    for other, score, matched_on in candidates_for(qasida, threshold):
        first, second = sorted((qasida, other), key=lambda work: work.pk)
        link, created = DuplicateLink.objects.get_or_create(
            first=first, second=second,
            defaults={'score': score, 'matched_on': matched_on},
        )
        if created:
            created_count += 1
        elif link.state == DuplicateLink.STATE_PENDING and link.score != score:
            link.score = score
            link.matched_on = matched_on
            link.save(update_fields=['score', 'matched_on'])
    return created_count


def scan(queryset=None, threshold=FUZZY_THRESHOLD):
    """Look for duplicates across a set of works. Returns links created."""
    from .models import Qasida

    if queryset is None:
        queryset = Qasida.objects.all()
    created_count = 0
    for qasida in queryset.exclude(dedup_signature='').iterator():
        created_count += record_duplicates(qasida, threshold)
    return created_count
