from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (AuthenticationForm, PasswordChangeForm,
                                       PasswordResetForm, SetPasswordForm,
                                       UserCreationForm)

from .models import Favourite, Qasida, ReaderProfile, Tag

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
