from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import Length
from django.shortcuts import get_object_or_404, redirect, render

from .forms import QasidaForm
from .models import Qasida, Suggestion, Tag
from .search import normalize

PAGE_SIZE = 24
FILTER_KEYS = ('q', 'lang', 'tag')

# The tag vocabulary is harvested from the source sites, so it arrives as
# prefixed slugs (maqam-hijaz, bahr-kamil). Group them and show readable names
# instead of one flat list of jargon.
LANGUAGE_TAGS = frozenset({
    'arabic', 'english', 'urdu', 'spanish', 'swedish', 'german', 'french', 'turkish',
})
FORM_TAGS = frozenset({
    'naat', 'qasida', 'manzhuma', 'mawlid-hadra', 'tawassul', 'around-the-year',
})
STATUS_LABELS = {
    'lyrics-in-images': 'Lyrics only as scans',
    'text-needs-review': 'Text needs review',
}


def _tag_group(name):
    if name.startswith('maqam-'):
        return 'Maqam (melodic mode)'
    if name.startswith('bahr-'):
        return 'Bahr (metre)'
    if name in STATUS_LABELS:
        return 'Condition'
    if name in LANGUAGE_TAGS:
        return 'Language'
    if name in FORM_TAGS or name.startswith('qasida-'):
        return 'Type'
    return 'Other'


def _tag_label(name):
    """Drop the taxonomy prefix; the group heading already carries it."""
    if name in STATUS_LABELS:
        return STATUS_LABELS[name]
    for prefix in ('maqam-', 'bahr-', 'qasida-'):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.replace('-', ' ').title()


GROUP_ORDER = ('Type', 'Language', 'Maqam (melodic mode)', 'Bahr (metre)', 'Condition', 'Other')


def _grouped_tag_facets(tags, active_tag):
    """Bucket the tag facets for display, flagging which group holds the selection."""
    buckets = {}
    for tag in tags:
        buckets.setdefault(_tag_group(tag.name), []).append({
            'name': tag.name,
            'label': _tag_label(tag.name),
            'n': tag.n,
            'is_active': tag.name.lower() == (active_tag or '').lower(),
        })
    groups = []
    for label in GROUP_ORDER:
        items = buckets.get(label)
        if not items:
            continue
        groups.append({
            'label': label,
            'items': items,
            'total': sum(i['n'] for i in items),
            'has_active': any(i['is_active'] for i in items),
        })
    return groups


def _read_filters(request):
    return {key: request.GET.get(key, '').strip() for key in FILTER_KEYS}


def _visible(request):
    """The rows this viewer may see: approved only, unless they are staff."""
    return Qasida.objects.visible_to(request.user)


def _apply_filters(request, filters, skip=()):
    """
    Build the queryset for `filters`, optionally ignoring one of them.

    Skipping a filter is what makes the facet counts honest: the tag counts are
    taken with the tag filter lifted, so each number answers "how many results
    if I pick this tag instead", never a library-wide total that cannot be
    reached from here.
    """
    qasidas = _visible(request)

    if filters['q'] and 'q' not in skip:
        # Match against the folded copy so a query typed without Arabic vowel
        # marks still finds vocalised text. Terms are ANDed, each hitting the
        # trigram index, so word order does not matter.
        for term in normalize(filters['q']).split():
            qasidas = qasidas.filter(search_text__contains=term)
    # Exact matches: these values come from the facet lists, and a substring
    # match on a short tag name matches almost everything.
    if filters['lang'] and 'lang' not in skip:
        qasidas = qasidas.filter(language__iexact=filters['lang'])
    if filters['tag'] and 'tag' not in skip:
        qasidas = qasidas.filter(tags__name__iexact=filters['tag'])

    return qasidas.distinct()


def _tag_facets(scope):
    """Tags present in `scope`, counted within it. Tags that would give no results are absent."""
    return (Tag.objects.filter(qasidas__in=scope)
            .annotate(n=Count('qasidas', distinct=True))
            .order_by('-n', 'name'))


def _language_facets(scope):
    return (scope.exclude(language='')
            .values('language')
            .annotate(n=Count('id', distinct=True))
            .order_by('-n', 'language'))


def _listing(request, heading):
    """Shared paginated listing with scoped facets, used for browsing and searching."""
    filters = _read_filters(request)
    active = {key: value for key, value in filters.items() if value}

    results = _apply_filters(request, filters).prefetch_related('tags', 'images').order_by('-created_at')
    paginator = Paginator(results, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    def querystring(drop=(), **overrides):
        params = {k: v for k, v in active.items() if k not in drop}
        params.update({k: v for k, v in overrides.items() if v})
        return urlencode(params)

    # Each facet is counted with its own dimension lifted, so switching within
    # a facet is always productive.
    tag_scope = _apply_filters(request, filters, skip=('tag',))
    language_scope = _apply_filters(request, filters, skip=('lang',))

    context = {
        'heading': heading,
        'page_obj': page_obj,
        'total': paginator.count,
        'query': filters['q'],
        'lang_filter': filters['lang'],
        'tag_filter': filters['tag'],
        'has_filters': bool(active),
        'querystring': querystring(),
        'qs_without_tag': querystring(drop=('tag', 'page')),
        'qs_without_lang': querystring(drop=('lang', 'page')),
        'tag_groups': _grouped_tag_facets(_tag_facets(tag_scope), filters['tag']),
        'all_languages': _language_facets(language_scope),
    }
    return render(request, 'core/listing.html', context)


# The landing page leads with ways in that suit a poetry library - who wrote
# it, what form it takes, which melodic mode it is sung in - rather than a
# reverse-chronological feed, which says nothing about the works themselves.
HOME_POET_COUNT = 8
HOME_FORM_COUNT = 10
HOME_MAQAM_COUNT = 8
HOME_FEATURED_PER_LANGUAGE = 3


def _top_poets(scope, limit):
    return (scope.exclude(author='')
            .values('author')
            .annotate(n=Count('id'))
            .order_by('-n', 'author')[:limit])


def _tags_in_group(scope, group_label, limit):
    """Facet-style tag list restricted to one of the display groups."""
    tags = [t for t in _tag_facets(scope) if _tag_group(t.name) == group_label]
    return [{'name': t.name, 'label': _tag_label(t.name), 'n': t.n} for t in tags[:limit]]


def _featured(scope, language, limit):
    """
    A few substantial works per language.

    Ordered by length so the picks are complete texts rather than fragments,
    and restricted to rows whose text was extracted cleanly.
    """
    return (scope.filter(language__iexact=language, text_quality=Qasida.TEXT_OK)
            .exclude(lyrics='')
            .prefetch_related('tags', 'images')
            .annotate(length=Length('lyrics'))
            .order_by('-length')[:limit])


def home(request):
    scope = _visible(request)
    languages = list(_language_facets(scope))
    featured = []
    for entry in languages[:2]:
        works = list(_featured(scope, entry['language'], HOME_FEATURED_PER_LANGUAGE))
        if works:
            featured.append({'language': entry['language'], 'total': entry['n'], 'works': works})

    return render(request, 'core/home.html', {
        'languages': languages,
        'poets': _top_poets(scope, HOME_POET_COUNT),
        'forms': _tags_in_group(scope, 'Type', HOME_FORM_COUNT),
        'maqamat': _tags_in_group(scope, 'Maqam (melodic mode)', HOME_MAQAM_COUNT),
        'featured_groups': featured,
        'transliterated_count': scope.exclude(transliteration='').count(),
    })


def browse(request):
    return _listing(request, 'Browse all qasidas')


def search(request):
    return _listing(request, 'Search')


def qasida_detail(request, pk):
    qasida = get_object_or_404(_visible(request), pk=pk)

    if request.method == 'POST':
        email = request.POST.get('email')
        suggested_lyrics = request.POST.get('suggested_lyrics')
        suggested_tags = request.POST.get('suggested_tags')

        if email:
            Suggestion.objects.create(
                qasida=qasida,
                email=email,
                suggested_lyrics=suggested_lyrics,
                suggested_tags=suggested_tags
            )
            messages.success(request, 'Your suggestion has been submitted for review.')
            return redirect('qasida_detail', pk=pk)
        else:
            messages.error(request, 'Email is required to submit a suggestion.')

    return render(request, 'core/detail.html', {'qasida': qasida})


@staff_member_required
def qasida_edit(request, pk):
    qasida = get_object_or_404(Qasida, pk=pk)
    form = QasidaForm(request.POST or None, instance=qasida)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Qasida updated.')
        return redirect('qasida_detail', pk=pk)
    return render(request, 'core/qasida_form.html', {'form': form, 'qasida': qasida})


@staff_member_required
def suggestion_inbox(request):
    """Review queue for reader-submitted corrections."""
    if request.method == 'POST':
        suggestion = get_object_or_404(Suggestion, pk=request.POST.get('suggestion'))
        if request.POST.get('action') == 'approve':
            suggestion.apply()
            messages.success(request, f'Applied the suggestion for "{suggestion.qasida}".')
        else:
            suggestion.reject()
            messages.success(request, 'Suggestion rejected.')
        return redirect('suggestion_inbox')

    pending = (Suggestion.objects.filter(is_reviewed=False)
               .select_related('qasida').order_by('created_at'))
    recent = (Suggestion.objects.filter(is_reviewed=True)
              .select_related('qasida').order_by('-created_at')[:20])
    return render(request, 'core/suggestions.html', {
        'pending': pending,
        'recent': recent,
        'pending_count': pending.count(),
    })


def random_qasida(request):
    """Send the reader to an arbitrary work - a way to browse without a query."""
    pick = (_visible(request).exclude(lyrics='')
            .filter(text_quality=Qasida.TEXT_OK)
            .order_by('?')
            .values_list('pk', flat=True)
            .first())
    if pick is None:
        return redirect('browse')
    return redirect('qasida_detail', pk=pick)


def poet(request, name):
    """Everything attributed to one poet."""
    works = (_visible(request).filter(author__iexact=name)
             .prefetch_related('tags', 'images')
             .order_by('title'))
    paginator = Paginator(works, PAGE_SIZE)
    return render(request, 'core/poet.html', {
        'poet_name': name,
        'page_obj': paginator.get_page(request.GET.get('page')),
        'total': paginator.count,
    })
