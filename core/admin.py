from django.contrib import admin
from .models import Tag, Qasida, Suggestion, SourceWebsite

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(Qasida)
class QasidaAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'language', 'created_at')
    list_filter = ('language', 'tags')
    search_fields = ('title', 'author', 'lyrics')
    filter_horizontal = ('tags',)

@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    list_display = ('qasida', 'email', 'is_reviewed', 'is_approved', 'created_at')
    list_filter = ('is_reviewed', 'is_approved')
    search_fields = ('email', 'suggested_lyrics', 'suggested_tags')
    actions = ['approve_suggestions', 'reject_suggestions']

    def approve_suggestions(self, request, queryset):
        for suggestion in queryset:
            suggestion.is_approved = True
            suggestion.is_reviewed = True
            # apply changes
            if suggestion.suggested_lyrics:
                suggestion.qasida.lyrics = suggestion.suggested_lyrics
            if suggestion.suggested_tags:
                new_tags = [t.strip() for t in suggestion.suggested_tags.split(',') if t.strip()]
                for nt in new_tags:
                    tag, _ = Tag.objects.get_or_create(name=nt)
                    suggestion.qasida.tags.add(tag)
            suggestion.qasida.save()
            suggestion.save()
        self.message_user(request, f"{queryset.count()} suggestions approved and applied.")
    approve_suggestions.short_description = "Approve and apply selected suggestions"

    def reject_suggestions(self, request, queryset):
        queryset.update(is_approved=False, is_reviewed=True)
        self.message_user(request, f"{queryset.count()} suggestions rejected.")
    reject_suggestions.short_description = "Reject selected suggestions"

@admin.register(SourceWebsite)
class SourceWebsiteAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'parser_type', 'is_active')
    list_filter = ('is_active', 'parser_type')
    search_fields = ('name', 'url')
