from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (AuthenticationForm, PasswordChangeForm,
                                       PasswordResetForm, SetPasswordForm,
                                       UserCreationForm)

from .models import Dedication, Favourite, Poet, Qasida, ReaderProfile, Tag

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
    # The dedication is chosen from a list, which is the point of it being a
    # list at all - but an editor who meets a name that is not on it yet has
    # to be able to add it here rather than abandon the record.
    new_dedication = forms.CharField(
        required=False,
        label='…or add a new dedication',
        help_text='Fill this in only if the name you want is not in the list above.',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'dir': 'auto'}),
    )
    new_poet = forms.CharField(
        required=False,
        label='…or add a new poet',
        help_text='Fill this in only if the poet you want is not in the list above.',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'dir': 'auto'}),
    )

    class Meta:
        model = Qasida
        fields = ['title', 'native_title', 'author', 'dedicated_to', 'language',
                  'text_quality', 'lyrics', 'transliteration', 'translation',
                  'translation_origin']
        widgets = {
            'title': forms.TextInput(attrs={'class': INPUT_CLASS, 'dir': 'auto'}),
            'native_title': forms.TextInput(attrs={'class': INPUT_CLASS, 'dir': 'rtl'}),
            'author': forms.Select(attrs={'class': INPUT_CLASS}),
            'dedicated_to': forms.Select(attrs={'class': INPUT_CLASS}),
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

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get('new_dedication') or '').strip()
        if name:
            # Matched without regard to case, so adding one that already
            # exists picks up the existing row instead of being refused by
            # the unique constraint.
            existing = Dedication.objects.filter(name__iexact=name).first()
            cleaned['dedicated_to'] = existing or Dedication.objects.create(name=name)

        poet = (cleaned.get('new_poet') or '').strip()
        if poet:
            cleaned['author'] = Poet.named(poet)
        return cleaned

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


class StyledFormMixin:
    """
    Give every field the shell's input styling.

    The site has one `.input` component class; without this each auth form
    would have to restate the widget attributes, and Django's own
    AuthenticationForm and SetPasswordForm cannot be given them at all
    without subclassing each one.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.RadioSelect)):
                continue
            field.widget.attrs.setdefault('class', INPUT_CLASS)


class RegistrationForm(StyledFormMixin, UserCreationForm):
    """
    Opening an account.

    Django's form asks for a username and a password twice, and runs the
    configured password policy for us. Two things are added: an email address,
    which is compulsory because it is the only way to recover an account, and
    uniqueness checks that ignore case on both fields - Django's own username
    constraint is case-sensitive, so without this "Ali" and "ali" become two
    accounts that nobody can tell apart.
    """

    email = forms.EmailField(
        required=True,
        help_text="Used to sign in, and to reset your password. Nothing else.")

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].help_text = (
            "Letters, digits and . @ + - _ ; this is what you will be known by.")

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        # An address as a username would make the sign-in field ambiguous:
        # one person's username could be another person's email.
        if '@' in username:
            raise forms.ValidationError(
                "Please choose a username without an @ sign. "
                "You will be able to sign in with your email address as well.")
        if get_user_model()._default_manager.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is taken.")
        return username

    def clean_email(self):
        email = get_user_model().objects.normalize_email(self.cleaned_data['email'].strip())
        if get_user_model()._default_manager.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "There is already an account with that email address. "
                "You can sign in, or reset the password.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class SignInForm(StyledFormMixin, AuthenticationForm):
    """The sign-in form, relabelled because either identifier is accepted."""

    remember_me = forms.BooleanField(
        required=False, initial=True, label="Keep me signed in on this device")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = "Username or email"
        self.fields['username'].widget.attrs['autocomplete'] = 'username'
        self.fields['password'].widget.attrs['autocomplete'] = 'current-password'

    # Django phrases this in terms of the username field alone.
    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': ("That username or email and password do not match an account. "
                          "Both are case sensitive, apart from the address itself."),
    }


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    pass


class StyledSetPasswordForm(StyledFormMixin, SetPasswordForm):
    pass


class StyledPasswordResetForm(StyledFormMixin, PasswordResetForm):
    """
    Ask for the address to send a reset link to.

    Django looks the address up case-sensitively by default, which quietly
    sends nothing when someone capitalises it differently from how they
    registered. Neither version tells the visitor whether an account exists.
    """

    def get_users(self, email):
        from django.contrib.auth.hashers import is_password_usable
        return (
            user for user in get_user_model()._default_manager.filter(
                email__iexact=email, is_active=True)
            if is_password_usable(user.password)
        )


class AccountEmailForm(StyledFormMixin, forms.ModelForm):
    """Changing the address an account is reachable at."""

    class Meta:
        model = get_user_model()
        fields = ('email',)
        help_texts = {'email': "Used to sign in and to reset your password."}

    def clean_email(self):
        email = get_user_model().objects.normalize_email(self.cleaned_data['email'].strip())
        if not email:
            raise forms.ValidationError("An email address is needed to recover the account.")
        clash = (get_user_model()._default_manager
                 .filter(email__iexact=email)
                 .exclude(pk=self.instance.pk))
        if clash.exists():
            raise forms.ValidationError("Another account already uses that address.")
        return email


class ReadingPreferencesForm(StyledFormMixin, forms.ModelForm):
    """How the verse is laid out for this reader, on every device they use."""

    class Meta:
        model = ReaderProfile
        fields = ('show_transliteration', 'show_translation', 'lyrics_size')


class FavouriteNoteForm(StyledFormMixin, forms.ModelForm):
    """A private line about why a work was saved."""

    class Meta:
        model = Favourite
        fields = ('note',)
        widgets = {'note': forms.TextInput(attrs={
            'placeholder': 'Why you saved this, for your own reference',
            'dir': 'auto'})}


def _axis_field(category, label):
    """One multi-select offering a single axis of the tag vocabulary."""
    return forms.ModelMultipleChoiceField(
        queryset=Tag.objects.filter(category=category),
        required=False,
        label=label,
        widget=forms.SelectMultiple(attrs={
            # Picked up by the admin script, which upgrades these to searchable
            # pickers; without it they stay ordinary multi-selects and work.
            'class': 'q-tag-select',
            'data-placeholder': f'Choose {label.lower()}\u2026',
            'size': 6,
        }),
    )


_AXIS_LABELS = dict(Tag.CATEGORY_CHOICES)


class QasidaAdminForm(forms.ModelForm):
    """
    The qasida form in the admin, with the tag vocabulary split by axis.

    Tags arrive from the source sites as one flat list mixing four unrelated
    things - what kind of poem it is, its language, the melodic mode it is
    sung in, the metre it is written in - plus a few describing the state of
    our own record. Offering all 65 in a single control means choosing a metre
    by scrolling past languages. One control per axis means each holds a
    couple of dozen related things, and an editor wanting a maqam looks only
    at maqams.

    Which axis a tag belongs to is stored on the tag (see Tag.category), so
    these are built from that rather than from a list repeated here. The
    fields are declared at class level rather than added in __init__ because
    the admin decides what to render from the form class, and a field that
    exists only on the instance is never shown.
    """

    # form field name -> the axis it offers
    TAG_FIELDS = (
        ('tags_form', Tag.CATEGORY_FORM),
        ('tags_language', Tag.CATEGORY_LANGUAGE),
        ('tags_maqam', Tag.CATEGORY_MAQAM),
        ('tags_bahr', Tag.CATEGORY_BAHR),
        ('tags_condition', Tag.CATEGORY_CONDITION),
        ('tags_other', Tag.CATEGORY_OTHER),
    )

    tags_form = _axis_field(Tag.CATEGORY_FORM, _AXIS_LABELS[Tag.CATEGORY_FORM])
    tags_language = _axis_field(Tag.CATEGORY_LANGUAGE, _AXIS_LABELS[Tag.CATEGORY_LANGUAGE])
    tags_maqam = _axis_field(Tag.CATEGORY_MAQAM, _AXIS_LABELS[Tag.CATEGORY_MAQAM])
    tags_bahr = _axis_field(Tag.CATEGORY_BAHR, _AXIS_LABELS[Tag.CATEGORY_BAHR])
    tags_condition = _axis_field(Tag.CATEGORY_CONDITION, _AXIS_LABELS[Tag.CATEGORY_CONDITION])
    tags_other = _axis_field(Tag.CATEGORY_OTHER, _AXIS_LABELS[Tag.CATEGORY_OTHER])

    class Meta:
        model = Qasida
        # Replaced by the per-axis fields above.
        exclude = ('tags',)

    @classmethod
    def populated_axes(cls):
        """
        The axes worth offering: the ones something is actually filed under.

        Used both to decide what the admin renders and, on save, which axes a
        submission speaks for - the two must agree, or an axis that was never
        shown would be read as "nothing chosen" and silently cleared.
        """
        present = set(Tag.objects.values_list('category', flat=True).distinct())
        return [(name, category) for name, category in cls.TAG_FIELDS
                if category in present]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            return
        chosen = set(self.instance.tags.values_list('pk', flat=True))
        for name, category in self.TAG_FIELDS:
            field = self.fields.get(name)
            if field is not None:
                field.initial = field.queryset.filter(pk__in=chosen)

    def _save_m2m(self):
        """
        Fold every axis back into the one relation the model actually has.

        Called on the admin's save path and by save(commit=True), so both
        reach here. Only the axes that were offered are read: an axis whose
        control was never rendered must keep whatever the work already
        carries, rather than being emptied by a save made for another reason.
        """
        super()._save_m2m()
        offered, chosen = [], []
        for name, category in self.populated_axes():
            offered.append(category)
            chosen.extend(self.cleaned_data.get(name) or [])

        untouched = list(self.instance.tags.exclude(category__in=offered))
        self.instance.tags.set(chosen + untouched)
