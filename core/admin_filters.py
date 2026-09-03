"""
Searchable admin filters.

Django's stock list filters render every distinct value as a link, which is
unusable for a field like author where there are hundreds. This provides a
single panel of text inputs instead: one card, one submit, rather than a
separate form and button per field.
"""

from django.contrib import admin

# field label -> (query parameter, ORM lookup)
TEXT_FILTERS = {
    'Author': ('author_contains', 'author__icontains'),
    'Title': ('title_contains', 'title__icontains'),
    'Tag': ('tag_contains', 'tags__name__icontains'),
    'Source URL': ('source_contains', 'source_url__icontains'),
}

# Lookups that can match a row more than once and need collapsing.
MULTI_VALUED = {'tags__name__icontains'}


class TextSearchPanel(admin.SimpleListFilter):
    """
    One filter object covering several typed fields.

    Registering four separate filters gave four separate forms stacked down the
    sidebar; this draws them together so the sidebar stays compact.
    """
    title = 'find'
    parameter_name = 'author_contains'  # the first, so Django keeps the filter
    template = 'admin/text_search_panel.html'

    def expected_parameters(self):
        # Every parameter this filter consumes, or Django rejects the others
        # as unrecognised query arguments.
        return [parameter for parameter, _ in TEXT_FILTERS.values()]

    def lookups(self, request, model_admin):
        # Django drops a filter whose lookups() is empty; the template ignores
        # this and draws input boxes.
        return ((None, None),)

    def choices(self, changelist):
        active = changelist.get_filters_params()
        consumed = set(self.expected_parameters())
        fields = []
        for label, (parameter, _) in TEXT_FILTERS.items():
            fields.append({
                'label': label,
                'name': parameter,
                'value': self.request.GET.get(parameter, ''),
            })
        return ({
            'fields': fields,
            # Carried as hidden inputs so typing here keeps the other filters.
            'other_params': [(key, value) for key, value in active.items()
                             if key not in consumed],
            'has_value': any(field['value'] for field in fields),
        },)

    def queryset(self, request, queryset):
        self.request = request
        for label, (parameter, lookup) in TEXT_FILTERS.items():
            value = (request.GET.get(parameter) or '').strip()
            if not value:
                continue
            queryset = queryset.filter(**{lookup: value})
            if lookup in MULTI_VALUED:
                queryset = queryset.distinct()
        return queryset

    def __init__(self, request, params, model, model_admin):
        # `choices` needs the request, which Django only hands to queryset().
        self.request = request
        super().__init__(request, params, model, model_admin)
        # SimpleListFilter consumes only its declared parameter_name. Anything
        # left in `params` is treated by the changelist as an invalid lookup and
        # the page redirects, so the rest are claimed here.
        for parameter in self.expected_parameters():
            if parameter in params:
                self.used_parameters[parameter] = params.pop(parameter)

    def has_output(self):
        return True
