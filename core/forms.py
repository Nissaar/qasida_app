from django import forms

from .models import Qasida, Tag

# The shell defines .input as a Tailwind component class, so widgets reuse it
# instead of restating utilities (and inheriting dark mode for free).
INPUT_CLASS = 'input'


class QasidaForm(forms.ModelForm):
    """Staff-facing editor. Tags are edited as free text rather than a 58-item
    multi-select, which is unusable once the harvested vocabulary grows."""

    tags_text = forms.CharField(
        required=False,
        label='Tags',
        help_text='Comma separated.',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS}),
    )

    class Meta:
        model = Qasida
        fields = ['title', 'arabic_title', 'author', 'language', 'text_quality',
                  'lyrics', 'transliteration', 'translation', 'translation_origin']
        widgets = {
            'title': forms.TextInput(attrs={'class': INPUT_CLASS, 'dir': 'auto'}),
            'arabic_title': forms.TextInput(attrs={'class': INPUT_CLASS, 'dir': 'rtl', 'lang': 'ar'}),
            'author': forms.TextInput(attrs={'class': INPUT_CLASS, 'dir': 'auto'}),
            'language': forms.TextInput(attrs={'class': INPUT_CLASS}),
            'text_quality': forms.Select(attrs={'class': INPUT_CLASS}),
            'lyrics': forms.Textarea(attrs={'class': INPUT_CLASS + ' font-naskh leading-loose',
                                            'rows': 22, 'dir': 'auto'}),
            'transliteration': forms.Textarea(attrs={'class': INPUT_CLASS + ' leading-loose',
                                                     'rows': 22, 'dir': 'ltr'}),
            'translation': forms.Textarea(attrs={'class': INPUT_CLASS + ' leading-loose',
                                                 'rows': 22, 'dir': 'ltr'}),
            'translation_origin': forms.Select(attrs={'class': INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['tags_text'].initial = ', '.join(
                self.instance.tags.order_by('name').values_list('name', flat=True))

    def save(self, commit=True):
        qasida = super().save(commit=commit)
        if commit:
            names = [n.strip() for n in self.cleaned_data['tags_text'].split(',') if n.strip()]
            tags = [Tag.objects.get_or_create(name=n)[0] for n in names]
            qasida.tags.set(tags)
            # Derive whatever is still missing, on the worker so the form
            # returns immediately.
            if not qasida.transliteration or not qasida.translation:
                from .tasks import enrich_qasida
                enrich_qasida.delay(qasida.pk, False)
        return qasida
