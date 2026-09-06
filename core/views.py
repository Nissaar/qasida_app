from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import Length
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import QasidaForm
from .models import (Collection, Favourite, Qasida, ReadingHistory,
                     Suggestion, Tag)
from .export import LAYERS, available_layers
from .pdf import build_pdf, filename_for
from .search import normalize

PAGE_SIZE = 24
FILTER_KEYS = ('q', 'lang', 'tag')

# The tag vocabulary arrives from the source sites as one flat list mixing four
# unrelated things - what kind of poem it is, its language, the melodic mode it
# is sung in, the metre it is written in. Which axis a tag sits on is now stored
# on the tag itself (see Tag.category), so these read it rather than guessing it
# from the name on every page view, and an editor's correction survives.
CATEGORY_LABELS = dict(Tag.CATEGORY_CHOICES)


def _grouped_tag_facets(tags, active_tags, request=None):
    """
    Bucket the tag facets by axis, flagging which groups hold a selection.

    Each entry carries the query string that turns it on or off, so the rail
    builds up a combination rather than replacing one choice with the next.
    """
    chosen = {name.lower() for name in (active_tags or [])}
    buckets = {}
    for tag in tags:
        buckets.setdefault(tag.category or Tag.CATEGORY_OTHER, []).append({
            'name': tag.name,
            'label': tag.label,
            'n': tag.n,
            'is_active': tag.name.lower() in chosen,
            'toggle_qs': _tag_toggle_query(request, tag.name) if request else '',
        })
    groups = []
    for category in Tag.CATEGORY_ORDER:
        items = buckets.get(category)
        if not items:
            continue
        items.sort(key=lambda item: (not item['is_active'], -item['n'], item['label']))
        groups.append({
            'label': CATEGORY_LABELS[category],
            'category': category,
            'items': items,
            'total': sum(i['n'] for i in items),
            'has_active': any(i['is_active'] for i in items),
        })
    return groups


def _read_filters(request):
    """
    The filters in force.

    Tags are a list: a work carries several, on different axes, so a reader
    narrowing to a form and then to a melodic mode is asking for both at once.
    Language stays single, because a work has exactly one.
    """
    seen, tags = set(), []
    for value in request.GET.getlist('tag'):
        value = value.strip()
        if value and value.lower() not in seen:
            seen.add(value.lower())
            tags.append(value)
    return {
        'q': request.GET.get('q', '').strip(),
        'lang': request.GET.get('lang', '').strip(),
        'author': request.GET.get('author', '').strip(),
        'tags': tags,
    }


def _query_params(request, drop=('page',)):
    """The current query string, minus `drop`, ready to be adjusted."""
    params = request.GET.copy()
    for key in drop:
        params.pop(key, None)
    # Blank values in a link read as noise and filter nothing.
    for key in list(params.keys()):
        values = [v for v in params.getlist(key) if v.strip()]
        if values:
            params.setlist(key, values)
        else:
            params.pop(key, None)
    return params


def _tag_toggle_query(request, name):
    """The query string with `name` added, or removed if it is already on."""
    params = _query_params(request)
    tags = [t for t in params.getlist('tag') if t.strip()]
    if name.lower() in {t.lower() for t in tags}:
        tags = [t for t in tags if t.lower() != name.lower()]
    else:
        tags = tags + [name]
    if tags:
        params.setlist('tag', tags)
    else:
        params.pop('tag', None)
    return params.urlencode()


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
    if filters['author'] and 'author' not in skip:
        qasidas = qasidas.filter(author__iexact=filters['author'])
    if filters['tags'] and 'tag' not in skip:
        # ANDed, and each as its own join: a single join with two conditions
        # asks for one tag that is both things at once, which nothing is.
        for name in filters['tags']:
            qasidas = qasidas.filter(tags__name__iexact=name)

    return qasidas.distinct()


def _tag_facets(scope):
    """Tags present in `scope`, counted within it. Tags that would give no results are absent."""
    return (Tag.objects.filter(qasidas__in=scope)
            .annotate(n=Count('qasidas', distinct=True))
            .order_by('-n', 'name'))


# The library holds 360 named poets, so the rail shows the ones present in the
# current results and sends the reader to the poets index for the rest. Scoped
# to the filters, the list is usually far shorter than this cap.
AUTHOR_FACET_LIMIT = 30


def _author_facets(scope, limit=AUTHOR_FACET_LIMIT):
    """Poets represented in `scope`, most prolific first, with a count."""
    return (scope.exclude(author='')
            .values('author')
            .annotate(n=Count('id', distinct=True))
            .order_by('-n', 'author')[:limit])


def _language_facets(scope):
    return (scope.exclude(language='')
            .values('language')
            .annotate(n=Count('id', distinct=True))
            .order_by('-n', 'language'))


def _listing(request, heading):
    """Shared paginated listing with scoped facets, used for browsing and searching."""
    filters = _read_filters(request)
    has_filters = bool(filters['q'] or filters['lang'] or filters['author']
                       or filters['tags'])

    results = (_apply_filters(request, filters)
               .prefetch_related('tags', 'images').order_by('-created_at'))
    paginator = Paginator(results, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    def without(*keys):
        return _query_params(request, drop=('page',) + keys).urlencode()

    # Tag counts are taken over the current results, because tags combine: the
    # number against an unchosen tag answers "how many of these also carry
    # this", which is what someone narrowing down needs to know. Language does
    # not combine - a work has one - so its counts are taken with the language
    # filter lifted, and each number answers "how many if I pick this instead".
    tag_scope = _apply_filters(request, filters)
    language_scope = _apply_filters(request, filters, skip=('lang',))
    # An author is single-valued, so picking one replaces rather than narrows;
    # its counts are taken with the author filter lifted, and each number
    # answers "how many if I pick this poet instead".
    author_scope = _apply_filters(request, filters, skip=('author',))

    active_tags = [{
        'name': name,
        'label': Tag.display_name(name),
        'remove_qs': _tag_toggle_query(request, name),
    } for name in filters['tags']]

    context = {
        'heading': heading,
        'page_obj': page_obj,
        'total': paginator.count,
        'query': filters['q'],
        'lang_filter': filters['lang'],
        'tag_filters': filters['tags'],
        'active_tags': active_tags,
        'author_filter': filters['author'],
        'has_filters': has_filters,
        'querystring': without(),
        'qs_without_lang': without('lang'),
        'qs_without_author': without('author'),
        'qs_without_q': without('q'),
        'tag_groups': _grouped_tag_facets(_tag_facets(tag_scope), filters['tags'], request),
        'all_languages': _language_facets(language_scope),
        'all_authors': _author_facets(author_scope),
        'author_total': (author_scope.exclude(author='')
                         .values('author').distinct().count()),
        'author_shown': AUTHOR_FACET_LIMIT,
    }
    return render(request, 'core/listing.html', context)


# The landing page leads with ways in that suit a poetry library - who wrote
# it, what form it takes, which melodic mode it is sung in - rather than a
# reverse-chronological feed, which says nothing about the works themselves.
HOME_POET_COUNT = 8
HOME_FORM_COUNT = 10
HOME_MAQAM_COUNT = 8
HOME_FEATURED_PER_LANGUAGE = 3
HOME_PERSONAL_COUNT = 4


def _top_poets(scope, limit):
    return (scope.exclude(author='')
            .values('author')
            .annotate(n=Count('id'))
            .order_by('-n', 'author')[:limit])


def _tags_in_group(scope, category, limit):
    """Facet-style tag list restricted to one axis."""
    tags = _tag_facets(scope).filter(category=category)[:limit]
    return [{'name': t.name, 'label': t.label, 'n': t.n} for t in tags]


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

    featured_collections = (Collection.objects
                            .annotate(n=Count('parts', filter=Q(parts__in=scope)))
                            .filter(n__gt=0).order_by('-n', 'name')[:6])

    # A signed-in reader is shown their own shelf where a visitor is shown the
    # case for having one. Both occupy the same place on the page, so the
    # invitation is not simply repeated at someone who has already accepted it.
    personal = {}
    if request.user.is_authenticated:
        personal = {
            'recent_reads': (ReadingHistory.objects.filter(user=request.user)
                             .select_related('qasida')[:HOME_PERSONAL_COUNT]),
            'recent_saves': (Favourite.objects.filter(user=request.user)
                             .select_related('qasida')[:HOME_PERSONAL_COUNT]),
            'saved_total': Favourite.objects.filter(user=request.user).count(),
        }

    return render(request, 'core/home.html', {
        'languages': languages,
        'collections': featured_collections,
        'poets': _top_poets(scope, HOME_POET_COUNT),
        'forms': _tags_in_group(scope, Tag.CATEGORY_FORM, HOME_FORM_COUNT),
        'maqamat': _tags_in_group(scope, Tag.CATEGORY_MAQAM, HOME_MAQAM_COUNT),
        'featured_groups': featured,
        'transliterated_count': scope.exclude(transliteration='').count(),
        **personal,
    })


def browse(request):
    return _listing(request, 'Browse all qasidas')


def search(request):
    return _listing(request, 'Search')


# Enough of a word to be worth a query. One letter matches most of the
# library and answers nothing.
SUGGEST_MIN_LENGTH = 2
SUGGEST_LIMIT = 8


def search_suggest(request):
    """
    Matches for the search box, as the reader types.

    Answers the same queryset the full search page would, so what appears
    under the box and what appears on the results page cannot disagree - and
    it goes through visible_to, so the review gate holds here too.
    """
    query = request.GET.get('q', '').strip()
    results, total = [], 0

    if len(query) >= SUGGEST_MIN_LENGTH:
        matches = _visible(request)
        for term in normalize(query).split():
            matches = matches.filter(search_text__contains=term)
        matches = matches.distinct()
        total = matches.count()
        for qasida in matches.order_by('title')[:SUGGEST_LIMIT]:
            results.append({
                'title': qasida.title or 'Untitled qasida',
                'arabic_title': qasida.arabic_title,
                'author': qasida.author,
                'language': qasida.language,
                'url': qasida.get_absolute_url(),
            })

    return JsonResponse({'query': query, 'total': total, 'results': results})


def qasida_by_id(request, pk):
    """
    The old numeric URL, kept working.

    Links to /qasida/<id>/ are already out in the world and cached by the
    service worker, so they redirect permanently to the slug instead of 404ing.
    """
    qasida = get_object_or_404(Qasida, pk=pk)
    return redirect('qasida_detail', slug=qasida.slug, permanent=True)


def qasida_detail(request, slug):
    qasida = get_object_or_404(_visible(request), slug=slug)

    if request.method == 'POST':
        # A signed-in reader is already reachable, so their address is taken
        # from the account rather than typed again; an anonymous one has no
        # other way to be followed up, so it stays compulsory for them.
        email = (request.POST.get('email') or '').strip()
        if not email and request.user.is_authenticated:
            email = request.user.email
        # Both default to empty rather than None: neither column accepts NULL,
        # so a form posted without one of them used to raise IntegrityError.
        suggested_lyrics = request.POST.get('suggested_lyrics') or ''
        suggested_tags = request.POST.get('suggested_tags') or ''

        if email or request.user.is_authenticated:
            Suggestion.objects.create(
                qasida=qasida,
                user=request.user if request.user.is_authenticated else None,
                email=email,
                suggested_lyrics=suggested_lyrics,
                suggested_tags=suggested_tags
            )
            messages.success(request, 'Your suggestion has been submitted for review.')
            return redirect('qasida_detail', slug=qasida.slug)
        else:
            messages.error(request, 'Email is required to submit a suggestion.')
    elif request.user.is_authenticated:
        # Only on a plain read, so a correction does not count as a visit.
        ReadingHistory.record(request.user, qasida)

    return render(request, 'core/detail.html', {'qasida': qasida})


@staff_member_required
def qasida_edit(request, slug):
    qasida = get_object_or_404(Qasida, slug=slug)
    form = QasidaForm(request.POST or None, instance=qasida)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Qasida updated.')
        return redirect('qasida_detail', slug=qasida.slug)
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
            .values_list('slug', flat=True)
            .first())
    if pick is None:
        return redirect('browse')
    return redirect('qasida_detail', slug=pick)


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


def poets(request):
    """Every poet the library holds, with how much of each it has."""
    scope = _visible(request)
    entries = (scope.exclude(author='')
               .values('author')
               .annotate(n=Count('id'))
               .order_by('-n', 'author'))
    return render(request, 'core/poets.html', {
        'poets': entries,
        'total_poets': len(entries),
        'unattributed': scope.filter(author='').count(),
    })


def categories(request):
    """
    The tag vocabulary, arranged by the groups used in the filter sidebar.

    Form, language, melodic mode and metre each read differently, so they are
    presented as separate sets rather than one long list.
    """
    scope = _visible(request)
    groups = _grouped_tag_facets(_tag_facets(scope), None)
    return render(request, 'core/categories.html', {
        'groups': groups,
        'total_tags': sum(len(group['items']) for group in groups),
        'languages': _language_facets(scope),
    })


def collections(request):
    """Works published in parts, such as the chapters of the Burdah."""
    scope = _visible(request)
    entries = (Collection.objects
               .annotate(n=Count('parts', filter=Q(parts__in=scope)))
               .filter(n__gt=0)
               .order_by('-n', 'name'))
    return render(request, 'core/collections.html', {'collections': entries})


def collection(request, slug):
    """One collection, with its parts in reading order."""
    item = get_object_or_404(Collection, slug=slug)
    parts = (_visible(request).filter(collection=item)
             .prefetch_related('tags', 'images')
             .order_by('collection_position', 'title'))
    return render(request, 'core/collection.html', {
        'collection': item,
        'parts': parts,
        'total': parts.count(),
    })


def qasida_download(request, slug):
    """
    Hand back the chosen layers of a work as a PDF.

    Which layers to include comes from the query string, so the same link can
    be shared for just the original, or the original beside its translation.
    """
    qasida = get_object_or_404(_visible(request), slug=slug)

    present = available_layers(qasida)
    asked = [name for name in LAYERS if request.GET.get(name) == '1']
    layers = [name for name in asked if name in present] or ['original']

    response = HttpResponse(build_pdf(qasida, layers), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename_for(qasida, layers)}"'
    return response
