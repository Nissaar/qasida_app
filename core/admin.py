from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.shortcuts import redirect, render
from django.urls import path
from django.db.models import Count, Max
from django.utils import timezone

from .admin_filters import TextSearchPanel
from .ocr_tool import OcrUploadForm, run_ocr
from .tasks import enrich_qasida

admin.site.site_header = "Qasida Library"
admin.site.site_title = "Qasida Library"
admin.site.index_title = "Library administration"
from .models import (Collection, Favourite, Tag, Qasida, QasidaImage, QasidaMedia,
                     ReadingHistory, ReaderProfile, Suggestion, SourceWebsite)

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

class QasidaImageInline(admin.TabularInline):
    model = QasidaImage
    extra = 0
    fields = ('image', 'caption', 'position', 'source_url')


class QasidaMediaInline(admin.TabularInline):
    model = QasidaMedia
    extra = 1
    fields = ('url', 'title', 'position', 'recognised_id')
    readonly_fields = ('recognised_id',)
    verbose_name = 'recording'
    verbose_name_plural = 'recordings (paste YouTube links)'

    @admin.display(description='Video id')
    def recognised_id(self, obj):
        # Shows whether the pasted link was understood, without saving first.
        return obj.video_id or '—'

@admin.register(Qasida)
class QasidaAdmin(admin.ModelAdmin):
    list_display = ('title', 'review_state', 'author', 'collection', 'language',
                    'source_site', 'text_quality', 'scan_count', 'has_latin',
                    'has_translation', 'saved_count')
    # The typed filters come first: author and tag have too many distinct
    # values for Django's default link-per-value rendering.
    list_filter = (TextSearchPanel, 'review_state', 'collection', 'source_site',
                   'language', 'text_quality', 'translation_origin')
    search_help_text = ('Searches title, Arabic title, author, lyrics and transliteration. '
                        'Need to read a scan? Use the Extract text tool at ./ocr-tool/')
    actions = ['approve_for_display', 'send_back_for_review', 'reject_qasidas',
               'enrich_selected', 'enrich_selected_overwrite',
               'add_to_collection', 'remove_from_collection']
    search_fields = ('title', 'arabic_title', 'author', 'lyrics', 'transliteration')
    list_select_related = ('source_site', 'collection')
    filter_horizontal = ('tags',)
    inlines = [QasidaMediaInline, QasidaImageInline]

    def get_queryset(self, request):
        # Annotated once for the whole page rather than counted per row.
        return super().get_queryset(request).annotate(_saves=Count('favourited_by', distinct=True))

    @admin.display(description='Scans')
    def scan_count(self, obj):
        return obj.images.count()

    @admin.display(description='Saved by', ordering='_saves')
    def saved_count(self, obj):
        """How many readers keep this. Sortable, so the column doubles as a
        way to find what is worth reviewing or correcting first."""
        return obj._saves

    @admin.display(description='Latin', boolean=True)
    def has_latin(self, obj):
        return bool(obj.transliteration)

    @admin.display(description='Translated', boolean=True)
    def has_translation(self, obj):
        return bool(obj.translation)

    @admin.action(description="Approve for display on the site")
    def approve_for_display(self, request, queryset):
        count = queryset.update(review_state=Qasida.REVIEW_APPROVED,
                                reviewed_at=timezone.now())
        self.message_user(request, f"{count} qasida(s) approved and now visible to readers.")

    @admin.action(description="Send back to Awaiting review")
    def send_back_for_review(self, request, queryset):
        count = queryset.update(review_state=Qasida.REVIEW_PENDING, reviewed_at=None)
        self.message_user(request, f"{count} qasida(s) hidden again pending review.")

    @admin.action(description="Transliterate and translate (fill blanks only)")
    def enrich_selected(self, request, queryset):
        for qasida in queryset:
            enrich_qasida.delay(qasida.pk, False)
        self.message_user(
            request,
            f"Queued {queryset.count()} qasida(s). The worker fills in the blanks; "
            f"reload in a moment to see them.")

    @admin.action(description="Transliterate and translate (redo, overwrites)")
    def enrich_selected_overwrite(self, request, queryset):
        for qasida in queryset:
            enrich_qasida.delay(qasida.pk, True)
        self.message_user(
            request,
            f"Queued {queryset.count()} qasida(s) for a full redo. A translation "
            f"published by a source is replaced too.")

    def get_urls(self):
        # An extra admin page rather than a separate app, so it inherits the
        # admin's login, styling and breadcrumbs.
        return [
            path('ocr-tool/', self.admin_site.admin_view(self.ocr_tool_view),
                 name='core_qasida_ocr_tool'),
        ] + super().get_urls()

    def ocr_tool_view(self, request):
        """Read text off an uploaded PDF or screenshot, for pasting into a record."""
        text, notes = '', []
        if request.method == 'POST':
            form = OcrUploadForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    text, notes = run_ocr(
                        form.cleaned_data['upload'],
                        form.cleaned_data['language'],
                        form.cleaned_data['keep_script_only'],
                        form.cleaned_data['page_segmentation'],
                    )
                except Exception as e:
                    notes = [f"Could not read that file: {type(e).__name__}: {e}"]
        else:
            form = OcrUploadForm()

        return render(request, 'admin/core/qasida/ocr_tool.html', {
            **self.admin_site.each_context(request),
            'title': 'Extract text from a PDF or image',
            'form': form,
            'extracted_text': text,
            'notes': notes,
            'opts': self.model._meta,
        })

    def save_model(self, request, obj, form, change):
        """Derive the missing fields whenever an editor saves a work by hand."""
        super().save_model(request, obj, form, change)
        if not obj.transliteration or not obj.translation:
            enrich_qasida.delay(obj.pk, False)

    @admin.action(description="Add selected to a collection…")
    def add_to_collection(self, request, queryset):
        """
        Attach the selected works to a collection, creating one if asked.

        Positions continue from whatever the collection already holds, so an
        existing reading order is not disturbed.
        """
        if 'apply' in request.POST:
            name = (request.POST.get('new_collection') or '').strip()
            chosen = request.POST.get('collection')

            if name:
                collection, _ = Collection.objects.get_or_create(name=name)
            elif chosen:
                collection = Collection.objects.filter(pk=chosen).first()
            else:
                collection = None

            if collection is None:
                self.message_user(request, "Pick a collection or type a name for a new one.",
                                  level='warning')
                return None

            start = (collection.parts.aggregate(top=Max('collection_position'))['top'] or 0)
            for offset, qasida in enumerate(queryset.order_by('title'), start=1):
                qasida.collection = collection
                qasida.collection_position = start + offset
                qasida.save(update_fields=['collection', 'collection_position'])

            self.message_user(
                request,
                f"Added {queryset.count()} work(s) to “{collection.name}”, "
                f"numbered from {start + 1}.")
            return redirect(request.get_full_path())

        return render(request, 'admin/core/qasida/add_to_collection.html', {
            **self.admin_site.each_context(request),
            'title': 'Add to a collection',
            'queryset': queryset,
            'collections': Collection.objects.annotate(n=Count('parts')).order_by('name'),
            'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
            'opts': self.model._meta,
        })

    @admin.action(description="Remove selected from their collection")
    def remove_from_collection(self, request, queryset):
        count = queryset.update(collection=None, collection_position=None)
        self.message_user(request, f"Removed {count} work(s) from their collection. "
                                   f"The works themselves are untouched.")

    @admin.action(description="Reject (keep, never display)")
    def reject_qasidas(self, request, queryset):
        count = queryset.update(review_state=Qasida.REVIEW_REJECTED,
                                reviewed_at=timezone.now())
        self.message_user(request, f"{count} qasida(s) rejected.")

@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    list_display = ('qasida', 'submitted_by', 'is_reviewed', 'is_approved', 'created_at')
    list_filter = ('is_reviewed', 'is_approved')
    search_fields = ('email', 'user__username', 'suggested_lyrics', 'suggested_tags')
    list_select_related = ('qasida', 'user')
    autocomplete_fields = ('user',)

    @admin.display(description='From', ordering='user__username')
    def submitted_by(self, obj):
        """The account it came from, or the address an anonymous sender left."""
        if obj.user:
            return f'{obj.user.username} ({obj.user.email})' if obj.user.email else obj.user.username
        return obj.email or 'anonymous'
    actions = ['approve_suggestions', 'reject_suggestions']

    def approve_suggestions(self, request, queryset):
        for suggestion in queryset:
            suggestion.apply()
        self.message_user(request, f"{queryset.count()} suggestions approved and applied.")
    approve_suggestions.short_description = "Approve and apply selected suggestions"

    def reject_suggestions(self, request, queryset):
        for suggestion in queryset:
            suggestion.reject()
        self.message_user(request, f"{queryset.count()} suggestions rejected.")
    reject_suggestions.short_description = "Reject selected suggestions"

@admin.register(SourceWebsite)
class SourceWebsiteAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'parser_type', 'is_active', 'qasida_count')
    list_filter = ('is_active', 'parser_type')
    search_fields = ('name', 'url')

    def get_queryset(self, request):
        # Annotate once rather than counting per row.
        return super().get_queryset(request).annotate(_qasidas=Count('qasidas'))

    @admin.display(description='Qasidas', ordering='_qasidas')
    def qasida_count(self, obj):
        return obj._qasidas


class CollectionPartInline(admin.TabularInline):
    model = Qasida
    fk_name = 'collection'
    extra = 0
    fields = ('collection_position', 'title', 'review_state')
    readonly_fields = ('title',)
    ordering = ('collection_position',)
    can_delete = False
    verbose_name = 'part'
    verbose_name_plural = 'parts, in reading order'
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        # Parts are attached from the qasida side, not created here.
        return False


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'arabic_name', 'part_count')
    search_fields = ('name', 'arabic_name')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [CollectionPartInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_parts=Count('parts'))

    @admin.display(description='Parts', ordering='_parts')
    def part_count(self, obj):
        return obj._parts


class ReaderProfileInline(admin.StackedInline):
    """A reader's layout preferences, alongside their account."""
    model = ReaderProfile
    can_delete = False
    verbose_name_plural = 'reading preferences'
    extra = 0


admin.site.unregister(get_user_model())


@admin.register(get_user_model())
class UserAdmin(DjangoUserAdmin):
    """
    Managing the people who use the site.

    Django's own user admin is kept underneath - it is what makes setting a
    password, the permission widgets and the "add user" flow work correctly -
    with the columns and filters an editor of this library actually wants:
    who is active, who joined when, and how much each account holds.

    What an account holds is deliberately shown as counts. An editor has no
    business reading an individual's saved list or reading history, and there
    is no admin page here that lets them: which works are popular is on the
    qasida list instead, as a total.
    """

    list_display = ('username', 'email', 'is_active', 'is_staff', 'is_superuser',
                    'saved_count', 'corrections_count', 'date_joined', 'last_login')
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'date_joined', 'groups')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('-date_joined',)
    list_per_page = 50
    inlines = [ReaderProfileInline]
    actions = ['activate_accounts', 'deactivate_accounts']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _saves=Count('favourites', distinct=True),
            _corrections=Count('suggestions', distinct=True),
        )

    @admin.display(description='Saved', ordering='_saves')
    def saved_count(self, obj):
        return obj._saves

    @admin.display(description='Corrections', ordering='_corrections')
    def corrections_count(self, obj):
        return obj._corrections

    def get_readonly_fields(self, request, obj=None):
        """
        Only a superuser may hand out staff or superuser status.

        Without this any staff member with permission to change users could
        promote themselves, which makes every other permission in the admin
        decorative.
        """
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly += ['is_superuser', 'is_staff', 'user_permissions', 'groups']
        return readonly

    def has_change_permission(self, request, obj=None):
        # A non-superuser cannot edit a superuser's account at all.
        if obj is not None and obj.is_superuser and not request.user.is_superuser:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_superuser and not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)

    @admin.action(description="Let these accounts sign in again")
    def activate_accounts(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} account(s) can sign in again.")

    @admin.action(description="Suspend these accounts (they keep everything)")
    def deactivate_accounts(self, request, queryset):
        """
        Stop an account signing in without destroying what it holds.

        Suspending rather than deleting is the reversible move, and the
        account doing the suspending is left out so nobody can lock themselves
        out of the admin in one click.
        """
        queryset = queryset.exclude(pk=request.user.pk)
        if not request.user.is_superuser:
            queryset = queryset.exclude(is_superuser=True)
        count = queryset.update(is_active=False)
        self.message_user(
            request,
            f"{count} account(s) suspended. Their saved works are untouched and "
            f"come back if the account is activated again.")
