from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import Length
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from . import notify, throttle
from .forms import (ContactForm, QasidaForm, QasidaRequestForm,
                    QasidaSubmissionForm)
from .models import (Collection, Contribution, Dedication, Favourite, Poet,
                     Qasida, ReadingHistory, SourceWebsite, Suggestion, Tag)
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
        'dedication': request.GET.get('dedication', '').strip(),
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
        qasidas = qasidas.filter(author__name__iexact=filters['author'])
    if filters['dedication'] and 'dedication' not in skip:
        qasidas = qasidas.filter(dedicated_to__name__iexact=filters['dedication'])
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
    return (Poet.objects.filter(qasidas__in=scope)
            .annotate(n=Count('qasidas', distinct=True))
            .order_by('-n', 'name')[:limit])


def _dedication_facets(scope, limit=AUTHOR_FACET_LIMIT):
    """Who the works in `scope` are written in praise of, most honoured first."""
    return (Dedication.objects.filter(qasidas__in=scope)
            .annotate(n=Count('qasidas', distinct=True))
            .order_by('-n', 'name')[:limit])


def _language_facets(scope):
    return (scope.exclude(language='')
            .values('language')
            .annotate(n=Count('id', distinct=True))
            .order_by('-n', 'language'))


def _listing(request, heading):
    """Shared paginated listing with scoped facets, used for browsing and searching."""
    filters = _read_filters(request)
    has_filters = bool(filters['q'] or filters['lang'] or filters['author']
                       or filters['dedication'] or filters['tags'])

    results = (_apply_filters(request, filters)
               .select_related('author', 'dedicated_to')
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
    dedication_scope = _apply_filters(request, filters, skip=('dedication',))

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
        'dedication_filter': filters['dedication'],
        'has_filters': has_filters,
        'querystring': without(),
        'qs_without_lang': without('lang'),
        'qs_without_author': without('author'),
        'qs_without_dedication': without('dedication'),
        'qs_without_q': without('q'),
        'tag_groups': _grouped_tag_facets(_tag_facets(tag_scope), filters['tags'], request),
        'all_languages': _language_facets(language_scope),
        'all_authors': _author_facets(author_scope),
        'author_total': Poet.objects.filter(qasidas__in=author_scope).distinct().count(),
        'author_shown': AUTHOR_FACET_LIMIT,
        'all_dedications': _dedication_facets(dedication_scope),
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
    return (Poet.objects.filter(qasidas__in=scope)
            .annotate(n=Count('qasidas', distinct=True))
            .order_by('-n', 'name')[:limit])


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
                'native_title': qasida.native_title,
                'author': qasida.author.name if qasida.author_id else '',
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


def _submitted(text):
    """
    A posted field, ready to compare against what is stored.

    Browsers send textarea content with CRLF line endings while the database
    holds LF, so without this every prefilled field would read as changed and
    each correction would carry a copy of the whole record.
    """
    return (text or '').replace('\r\n', '\n').replace('\r', '\n').strip()


def _suggested_changes(request, qasida):
    """Only the fields the sender actually altered."""
    changed = {}
    for field, target, _label in Suggestion.FIELDS:
        proposed = _submitted(request.POST.get(field))
        current = getattr(qasida, target)
        # The poet is a relation, so compare against its name rather than
        # against the object, whose repr would never match what was typed.
        current = current.name if isinstance(current, Poet) else current
        if proposed and proposed != _submitted(current):
            changed[field] = proposed
    return changed


def qasida_detail(request, slug):
    qasida = get_object_or_404(_visible(request), slug=slug)

    if request.method == 'POST':
        # A signed-in reader is already reachable, so their address is taken
        # from the account rather than typed again; an anonymous one has no
        # other way to be followed up, so it stays compulsory for them.
        email = (request.POST.get('email') or '').strip()
        if not email and request.user.is_authenticated:
            email = request.user.email
        changes = _suggested_changes(request, qasida)
        # Tags are additive rather than a replacement, so they are taken as
        # sent rather than compared against what the work already carries.
        suggested_tags = _submitted(request.POST.get('suggested_tags'))
        note = _submitted(request.POST.get('note'))

        if not (email or request.user.is_authenticated):
            messages.error(request, 'Email is required to submit a suggestion.')
        elif not (changes or suggested_tags or note):
            messages.error(
                request,
                'Nothing was changed, so there is nothing to review. Edit a '
                'field, add a tag, or describe what is wrong.')
        else:
            suggestion = Suggestion.objects.create(
                qasida=qasida,
                user=request.user if request.user.is_authenticated else None,
                email=email,
                suggested_tags=suggested_tags,
                note=note,
                **changes,
            )
            # The correction is already stored; telling an editor about it is
            # the part that can fail, and notify swallows that rather than
            # losing the correction to a mail server being down.
            notify.suggestion_received(suggestion, request)
            messages.success(request, 'Thank you. Your correction has been sent for review.')
            return redirect('qasida_detail', slug=qasida.slug)
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
    works = (_visible(request).filter(author__name__iexact=name)
             .select_related('author')
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
    entries = (Poet.objects.filter(qasidas__in=scope)
               .annotate(n=Count('qasidas', distinct=True))
               .order_by('-n', 'name'))
    return render(request, 'core/poets.html', {
        'poets': entries,
        'total_poets': entries.count(),
        'unattributed': scope.filter(author__isnull=True).count(),
    })


def dedications(request):
    """
    Everyone the library's works are written in praise of.

    Much of this repertoire is grouped by whom it honours rather than by who
    wrote it, which is a way in the site did not offer until now.
    """
    scope = _visible(request)
    entries = (Dedication.objects.filter(qasidas__in=scope)
               .annotate(n=Count('qasidas', distinct=True))
               .order_by('-n', 'name'))
    return render(request, 'core/dedications.html', {
        'dedications': entries,
        'total_dedications': entries.count(),
        'undedicated': scope.filter(dedicated_to__isnull=True).count(),
    })


def dedication(request, name):
    """Everything written in praise of one person."""
    honoured = get_object_or_404(Dedication, name__iexact=name)
    works = (_visible(request).filter(dedicated_to=honoured)
             .select_related('author', 'dedicated_to')
             .prefetch_related('tags', 'images')
             .order_by('title'))
    paginator = Paginator(works, PAGE_SIZE)
    return render(request, 'core/dedication.html', {
        'dedication': honoured,
        'page_obj': paginator.get_page(request.GET.get('page')),
        'total': paginator.count,
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

    # Where a work is held as scanned pages, the scan is not decoration for the
    # text - it is what the text was read off, and the only reliable record
    # where the reading is doubtful. Included unless the reader opts out.
    include_scans = request.GET.get('scans', '1') == '1'

    response = HttpResponse(build_pdf(qasida, layers, include_scans),
                            content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename_for(qasida, layers)}"'
    return response


# --------------------------------------------------------------------------
# About, privacy, and writing in
# --------------------------------------------------------------------------

def about(request):
    """What this library is, where its texts come from, and who keeps it."""
    scope = _visible(request)
    return render(request, 'core/about.html', {
        'poet_count': Poet.objects.filter(qasidas__in=scope).distinct().count(),
        'language_count': scope.exclude(language='').values('language').distinct().count(),
        'translated_count': scope.exclude(translation='').count(),
        'transliterated_count': scope.exclude(transliteration='').count(),
        'sources': SourceWebsite.objects.filter(is_active=True).order_by('name'),
    })


def privacy(request):
    """What the site stores about a reader, and what it does not."""
    return render(request, 'core/privacy.html', {})


# How many messages one address, or one account, may send before the form
# starts refusing. Generous for a person - nobody writes to a library six
# times in an hour - and low enough that a script gets nowhere.
CONTACT_LIMIT = 6
CONTACT_WINDOW = 60 * 60


def contact(request):
    """
    Write to the library.

    The message is saved before it is mailed, and mailing failing does not
    fail the request: a mail relay refusing connections must not turn someone
    taking the trouble to write in into an error page and a lost message.
    """
    keys = throttle.keys_for(request, 'contact')
    form = ContactForm(request.POST or None, user=request.user)

    if request.method == 'POST':
        if any(throttle.over_limit(key, CONTACT_LIMIT) for key in keys):
            messages.error(
                request,
                'That is several messages in a short time. Please wait an hour, '
                f'or write straight to {settings.CONTACT_EMAIL}.')
        elif form.is_valid():
            message = form.save(commit=False)
            if request.user.is_authenticated:
                message.user = request.user
            message.save()
            for key in keys:
                throttle.record(key, CONTACT_WINDOW)
            notify.contact_received(message, request)
            messages.success(
                request,
                'Thank you - your message has arrived. You will get a reply at '
                f'{message.email}.')
            return redirect('contact')

    return render(request, 'core/contact.html', {'form': form})


# --------------------------------------------------------------------------
# Contributions: asking for a work, and sending one in
# --------------------------------------------------------------------------

# Per account and per address, in a day. A prolific contributor sending in a
# dozen texts is exactly what this library wants; a thousand is a script.
CONTRIBUTION_LIMIT = 20
CONTRIBUTION_WINDOW = 60 * 60 * 24


def contribute(request):
    """
    The two ways in, side by side.

    A landing page rather than sending people straight to a form, because
    which of the two someone wants depends on something they may not have
    thought about yet: whether they have the text in front of them.
    """
    mine = None
    if request.user.is_authenticated:
        mine = (Contribution.objects.filter(user=request.user)
                .order_by('-created_at')[:5])
    return render(request, 'core/contribute.html', {'mine': mine})


def _contribution_view(request, form_class, template, heading, lead):
    """
    The shared body of the request and submission forms.

    Both save a Contribution, count it against the sender's allowance, tell an
    editor, and send the reader to their own list where they can see it
    waiting. Only the form class and the words around it differ.
    """
    keys = throttle.keys_for(request, 'contribution')
    form = form_class(request.POST or None)

    if request.method == 'POST':
        if any(throttle.over_limit(key, CONTRIBUTION_LIMIT) for key in keys):
            messages.error(
                request,
                'That is a great deal in one day. Please carry on tomorrow, or '
                f'write to {settings.CONTACT_EMAIL} and we will sort it out.')
        elif form.is_valid():
            contribution = form.save(user=request.user)
            for key in keys:
                throttle.record(key, CONTRIBUTION_WINDOW)
            notify.contribution_received(contribution, request)
            messages.success(
                request,
                'Thank you. An editor will read it, and you can follow what '
                'happens to it here.')
            return redirect('my_contributions')

    # Offered to the language field as a datalist, so the spellings already in
    # the library are one keystroke away and a new one is still typeable.
    languages = (_visible(request).exclude(language='')
                 .values_list('language', flat=True).distinct().order_by('language'))

    return render(request, template, {
        'form': form,
        'heading': heading,
        'lead': lead,
        'known_languages': languages,
    })


@login_required
def request_qasida(request):
    """Ask the library for a work it does not hold."""
    return _contribution_view(
        request, QasidaRequestForm, 'core/contribution_form.html',
        heading='Request a qasida',
        lead=('Tell us what you are looking for and we will try to find it, '
              'read it and add it. Anything you know helps - a line, a poet, '
              'where you heard it.'))


@login_required
def submit_qasida(request):
    """Send in a text for an editor to read and publish."""
    return _contribution_view(
        request, QasidaSubmissionForm, 'core/contribution_form.html',
        heading='Submit a qasida',
        lead=('Send in a text you have. An editor reads everything before it '
              'goes on the site, so nothing you send appears unchecked - and '
              'where it comes from matters as much as the verses themselves.'))


@staff_member_required
def contribution_inbox(request):
    """Review queue for what readers have asked for and sent in."""
    if request.method == 'POST':
        contribution = get_object_or_404(Contribution, pk=request.POST.get('contribution'))
        action = request.POST.get('action')
        note = (request.POST.get('staff_note') or '').strip()

        if action == 'publish' and contribution.can_publish():
            qasida = contribution.publish(by=request.user)
            notify.contribution_decided(contribution, request)
            messages.success(
                request,
                f'Created a record from "{contribution.display_title}". Read it '
                f'through and approve it, and it goes on the site.')
            return redirect('qasida_edit', slug=qasida.slug)
        if action == 'accept':
            contribution.accept(by=request.user, note=note)
            notify.contribution_decided(contribution, request)
            messages.success(request, f'Accepted "{contribution.display_title}".')
        else:
            contribution.decline(by=request.user, note=note)
            notify.contribution_decided(contribution, request)
            messages.success(request, f'Declined "{contribution.display_title}".')
        return redirect('contribution_inbox')

    waiting = (Contribution.objects.filter(status=Contribution.STATUS_PENDING)
               .select_related('user').order_by('created_at'))
    decided = (Contribution.objects.exclude(status=Contribution.STATUS_PENDING)
               .select_related('user', 'published_as', 'reviewed_by')[:20])
    return render(request, 'core/contributions.html', {
        'waiting': waiting,
        'decided': decided,
        'waiting_count': waiting.count(),
    })
