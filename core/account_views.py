"""
Everything a signed-in reader can do.

Kept apart from `views`, which is about the library itself: these pages are
about one person's relationship to it - what they saved, what they read, what
they suggested, and how they want it laid out.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (LoginView, PasswordResetConfirmView,
                                       PasswordResetView)
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import throttle, verification
from .forms import (AccountEmailForm, FavouriteNoteForm, ReadingPreferencesForm,
                    RegistrationForm, SignInForm)
from .models import (Contribution, Favourite, Qasida, ReaderProfile,
                     ReadingHistory, Suggestion)
from .search import normalize

PAGE_SIZE = 24

# The backend a freshly registered account is logged in with. `login()` needs
# to be told which one when several are configured and no `authenticate()`
# call has already stamped it on the user.
DEFAULT_BACKEND = 'core.auth_backends.UsernameOrEmailBackend'


# --------------------------------------------------------------------------
# Sign-in attempt limiting
#
# Counted in the cache, keyed on both the address and the identifier typed, so
# neither a single host walking a password list nor a spread of hosts working
# on one account gets an unlimited number of guesses. Every cache call is
# wrapped: if Redis is unreachable the site keeps signing people in, it simply
# stops counting, which is the right way round for a library.
# --------------------------------------------------------------------------

def _attempt_keys(request):
    """The counters this attempt touches, each with the limit that applies."""
    identifier = (request.POST.get('username') or '').strip().lower()[:150]
    keys = [(f'login-fail:ip:{throttle.client_ip(request)}',
             getattr(settings, 'LOGIN_ATTEMPT_LIMIT_PER_IP', 40))]
    if identifier:
        keys.append((f'login-fail:id:{identifier}',
                     getattr(settings, 'LOGIN_ATTEMPT_LIMIT', 8)))
    return keys


def _too_many_attempts(request):
    return any(throttle.over_limit(key, limit) for key, limit in _attempt_keys(request))


def _note_failed_attempt(request):
    window = getattr(settings, 'LOGIN_ATTEMPT_WINDOW', 15 * 60)
    for key, _limit in _attempt_keys(request):
        throttle.record(key, window)


def _clear_failed_attempts(request):
    for key, _limit in _attempt_keys(request):
        throttle.clear(key)


class SignInView(LoginView):
    """Sign in with a username or an email address."""

    form_class = SignInForm
    template_name = 'core/account/login.html'
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        if _too_many_attempts(request):
            form = self.get_form()
            form.full_clean()
            form.add_error(None,
                           "Too many sign-in attempts. Wait a few minutes and try again, "
                           "or reset your password.")
            return self.form_invalid(form)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        _clear_failed_attempts(self.request)
        response = super().form_valid(form)
        if not form.cleaned_data.get('remember_me'):
            # Ends with the browser rather than in a fortnight, which is what
            # someone on a shared machine is asking for by unticking it.
            self.request.session.set_expiry(0)
        return response

    def form_invalid(self, form):
        _note_failed_attempt(self.request)
        return super().form_invalid(form)


# Accounts opened from one address in an hour. A household or a mosque
# signing up together stays well under it; a script creating accounts to get
# round the per-account limits elsewhere does not.
REGISTER_LIMIT = 10
REGISTER_WINDOW = 60 * 60


def register(request):
    """Open an account, and sign in with it straight away."""
    if request.user.is_authenticated:
        return redirect('my_library')

    form = RegistrationForm(request.POST or None)
    key = f'register:ip:{throttle.client_ip(request)}'
    if request.method == 'POST' and throttle.over_limit(key, REGISTER_LIMIT):
        form.is_valid()
        form.add_error(None, "Several accounts have been opened from here in the last "
                             "hour. Please try again later.")
    elif request.method == 'POST' and form.is_valid():
        throttle.record(key, REGISTER_WINDOW)
        user = form.save()
        # The one path where a stranger types an address: it waits for its
        # owner to confirm it. See core.verification.
        ReaderProfile.objects.update_or_create(user=user, defaults={'email_verified': False})
        verification.send_link(user, user.email, request)
        login(request, user, backend=DEFAULT_BACKEND)
        messages.success(
            request,
            f"Welcome, {user.username}. Anything you save is now kept to your account. "
            f"We have sent a link to {user.email} to confirm the address is yours.")
        return redirect(_safe_next(request, fallback='my_library'))

    return render(request, 'core/account/register.html', {'form': form})


# Reset emails asked for in an hour: per address typed, so nobody can fill a
# stranger's inbox with them, and per visitor, so the form cannot be used to
# send mail to a list of addresses. Both answer with the same page whether or
# not an account exists, so the limit reveals nothing about who has one.
RESET_LIMIT_PER_EMAIL = 3
RESET_LIMIT_PER_IP = 10
RESET_WINDOW = 60 * 60


class RateLimitedPasswordResetView(PasswordResetView):
    """Django's reset view, with a cap on how much mail it can be made to send."""

    def form_valid(self, form):
        email = form.cleaned_data['email'].strip().lower()
        keys = [(f'reset:email:{email}', RESET_LIMIT_PER_EMAIL),
                (f'reset:ip:{throttle.client_ip(self.request)}', RESET_LIMIT_PER_IP)]
        if any(throttle.over_limit(key, limit) for key, limit in keys):
            form.add_error(None, "A reset link has been asked for several times in the "
                                 "last hour. Check your inbox, including spam, or try "
                                 "again later.")
            return self.form_invalid(form)
        for key, _limit in keys:
            throttle.record(key, RESET_WINDOW)
        return super().form_valid(form)


class VerifyingPasswordResetConfirmView(PasswordResetConfirmView):
    """
    Django's reset confirmation, which also confirms the address.

    The reset link was sent to the account's address and followed from it,
    which is exactly what a confirmation link proves.
    """

    def form_valid(self, form):
        response = super().form_valid(form)
        verification.mark_verified_by_reset(form.user)
        return response


def verify_email(request, token):
    """Follow a link sent to confirm an address."""
    user, outcome = verification.confirm(token)
    if outcome == verification.CHANGED:
        messages.success(request, f'Your address is now {user.email}.')
    elif outcome == verification.VERIFIED:
        messages.success(request, f'Thank you - {user.email} is confirmed.')
    elif outcome == verification.TAKEN:
        messages.error(request, 'Another account has already confirmed that address, so '
                                'it cannot be used for this one.')
    else:
        messages.error(request, 'That link has expired or has already been used. '
                                'You can ask for a new one from your account settings.')
    if request.user.is_authenticated:
        return redirect('account_settings')
    return redirect('login')


# Confirmation links one account may ask for in an hour.
RESEND_LIMIT = 3
RESEND_WINDOW = 60 * 60


@require_POST
@login_required
def resend_verification(request):
    """Send the confirmation link again, to the address that is waiting."""
    key = f'verify-resend:user:{request.user.pk}'
    profile = ReaderProfile.for_user(request.user)
    target = profile.pending_email or ('' if profile.email_verified else request.user.email)
    if not target:
        messages.info(request, 'Your address is already confirmed.')
    elif throttle.over_limit(key, RESEND_LIMIT):
        messages.error(request, 'Several links have been sent in the last hour. Check '
                                'your inbox, including spam, or try again later.')
    else:
        throttle.record(key, RESEND_WINDOW)
        verification.send_link(request.user, target, request)
        messages.success(request, f'We have sent a new link to {target}.')
    return redirect('account_settings')


def _safe_next(request, fallback):
    """
    Where to send someone after an action, refusing anywhere off this site.

    An unchecked `next` is an open redirect: a link into our own sign-in page
    could land the visitor somewhere else entirely, wearing our name.
    """
    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return reverse(fallback) if '/' not in fallback else fallback


# --------------------------------------------------------------------------
# Favourites
# --------------------------------------------------------------------------

@require_POST
@login_required
def toggle_favourite(request, slug):
    """
    Save or unsave a work.

    POST only, so it cannot be triggered by a link someone else planted, and
    limited to works the reader is allowed to see, so the review gate is not
    a way to discover unapproved rows. Answers JSON when asked, which is what
    lets the button work without leaving the page, and otherwise redirects
    back to where the reader pressed it.
    """
    qasida = get_object_or_404(Qasida.objects.visible_to(request.user), slug=slug)

    existing = Favourite.objects.filter(user=request.user, qasida=qasida).first()
    if existing:
        existing.delete()
        saved = False
    else:
        try:
            Favourite.objects.create(user=request.user, qasida=qasida)
        except IntegrityError:
            pass  # double-submitted; it is saved either way
        saved = True

    if request.headers.get('X-Requested-With') == 'fetch':
        return JsonResponse({'saved': saved,
                             'count': Favourite.objects.filter(user=request.user).count()})

    messages.success(request,
                     'Saved to your library.' if saved else 'Removed from your library.')
    return redirect(_safe_next(request, fallback=qasida.get_absolute_url()))


@never_cache
@login_required
def my_library(request):
    """The works this reader saved, newest first, with their notes."""
    saved = (Favourite.objects
             .filter(user=request.user)
             .select_related('qasida', 'qasida__collection')
             .prefetch_related('qasida__tags', 'qasida__images'))

    query = request.GET.get('q', '').strip()
    if query:
        # Folded the same way as the library's own search, so a term typed
        # without Arabic vowel marks finds the vocalised text here too.
        for term in normalize(query).split():
            saved = saved.filter(qasida__search_text__contains=term)

    paginator = Paginator(saved, PAGE_SIZE)
    return render(request, 'core/account/library.html', {
        'page_obj': paginator.get_page(request.GET.get('page')),
        'total': paginator.count,
        'query': query,
        'tab': 'saved',
    })


@require_POST
@login_required
def favourite_note(request, pk):
    """Attach or change the reader's own note on something they saved."""
    favourite = get_object_or_404(Favourite, pk=pk, user=request.user)
    form = FavouriteNoteForm(request.POST, instance=favourite)
    if form.is_valid():
        form.save()
        messages.success(request, 'Note saved.')
    else:
        messages.error(request, 'That note was too long to save.')
    return redirect('my_library')


# --------------------------------------------------------------------------
# History and corrections
# --------------------------------------------------------------------------

@never_cache
@login_required
def my_history(request):
    """What this reader has opened, most recent first."""
    history = (ReadingHistory.objects
               .filter(user=request.user)
               .select_related('qasida')
               .prefetch_related('qasida__tags', 'qasida__images'))
    paginator = Paginator(history, PAGE_SIZE)
    return render(request, 'core/account/history.html', {
        'page_obj': paginator.get_page(request.GET.get('page')),
        'total': paginator.count,
        'tab': 'history',
    })


@require_POST
@login_required
def clear_history(request):
    removed, _ = ReadingHistory.objects.filter(user=request.user).delete()
    messages.success(request, f'Cleared {removed} item(s) from your reading history.')
    return redirect('my_history')


@never_cache
@login_required
def my_corrections(request):
    """
    Corrections this reader submitted, and what became of them.

    The suggestion form has always accepted corrections; until now there was
    no way for the person who sent one to learn whether it was used.
    """
    corrections = (Suggestion.objects
                   .filter(user=request.user)
                   .select_related('qasida')
                   .order_by('-created_at'))
    paginator = Paginator(corrections, PAGE_SIZE)
    return render(request, 'core/account/corrections.html', {
        'page_obj': paginator.get_page(request.GET.get('page')),
        'total': paginator.count,
        'tab': 'corrections',
    })


@never_cache
@login_required
def my_contributions(request):
    """
    What this reader has asked for and sent in, and where each one stands.

    The point of the page is the status column. Someone who types out a
    qasida from a book is doing unpaid work for a library, and the least it
    owes them is a place that says whether it was read, what became of it,
    and - once it is up - a link to the thing they helped make.
    """
    contributions = (Contribution.objects
                     .filter(user=request.user)
                     .select_related('published_as')
                     .order_by('-created_at'))
    paginator = Paginator(contributions, PAGE_SIZE)
    return render(request, 'core/account/contributions.html', {
        'page_obj': paginator.get_page(request.GET.get('page')),
        'total': paginator.count,
        'waiting': contributions.filter(status=Contribution.STATUS_PENDING).count(),
        'accepted': contributions.filter(status=Contribution.STATUS_ACCEPTED).count(),
        'tab': 'contributions',
    })


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

@never_cache
@login_required
def account_settings(request):
    """Address, reading preferences, and the way out."""
    profile = ReaderProfile.for_user(request.user)
    email_form = AccountEmailForm(instance=request.user)
    preferences_form = ReadingPreferencesForm(instance=profile)

    if request.method == 'POST':
        if 'save_email' in request.POST:
            email_form = AccountEmailForm(request.POST, instance=request.user)
            if email_form.is_valid():
                new = email_form.cleaned_data['email']
                request.user.refresh_from_db(fields=['email'])
                if new.lower() == request.user.email.lower():
                    profile.pending_email = ''
                    profile.save(update_fields=['pending_email'])
                    messages.info(request, 'That is already the address on this account.')
                else:
                    # Held until confirmed: the account keeps its current
                    # address, and so its way back in, until the new one is
                    # shown to belong to its owner.
                    profile.pending_email = new
                    profile.save(update_fields=['pending_email'])
                    verification.send_link(request.user, new, request)
                    messages.success(
                        request, f'We have sent a link to {new}. Your address changes '
                                 f'when you follow it.')
                return redirect('account_settings')
        elif 'save_preferences' in request.POST:
            preferences_form = ReadingPreferencesForm(request.POST, instance=profile)
            if preferences_form.is_valid():
                preferences_form.save()
                messages.success(request, 'Reading preferences saved.')
                return redirect('account_settings')

    return render(request, 'core/account/settings.html', {
        'email_form': email_form,
        'profile': profile,
        'email_verified': profile.email_verified,
        'preferences_form': preferences_form,
        'tab': 'settings',
        'saved_count': Favourite.objects.filter(user=request.user).count(),
        'history_count': ReadingHistory.objects.filter(user=request.user).count(),
        'corrections_count': Suggestion.objects.filter(user=request.user).count(),
        'contributions_count': Contribution.objects.filter(user=request.user).count(),
    })


@never_cache
@login_required
def delete_account(request):
    """
    Close an account.

    The password is asked for again, because a session left open on a shared
    machine should not be enough to destroy someone's library. Saved works,
    reading history and preferences go with the account; corrections already
    published stay, detached from the person who sent them, because they have
    become part of the library's text.
    """
    if request.method == 'POST':
        if not request.user.check_password(request.POST.get('password', '')):
            messages.error(request, 'That password is not right, so nothing was deleted.')
            return redirect('delete_account')

        username = request.user.username
        user = request.user
        logout(request)
        user.delete()
        messages.success(request, f'The account "{username}" and everything saved to it are gone.')
        return redirect('home')

    return render(request, 'core/account/delete.html', {
        'tab': 'settings',
        'saved_count': Favourite.objects.filter(user=request.user).count(),
    })
