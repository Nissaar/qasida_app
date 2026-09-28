"""
Confirming that an email address belongs to the person who typed it.

Anyone could open an account, or change an account's address, to an address
they did not own. The owner of that address was then told it was taken when
they came to sign up, signing in by that address reached the stranger's
account, and editors' replies about a contribution went to whoever the
address belonged to rather than to the person who sent it.

An address typed into the sign-up form or the settings page is therefore
confirmed by a link sent to it, and until it is:

- it cannot be used to sign in (the username still can);
- it does not count as taken, so its real owner can still sign up with it;
- the library sends it nothing but the confirmation itself.

A changed address is held as pending and the account keeps its old one until
the link is followed. Accounts that existed before this, and those made by
staff, are taken as confirmed. The link is a signed token, so nothing needs
storing, and it names the address it confirms, so a link for an address the
account has since moved away from confirms nothing.
"""

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse

from . import notify
from .models import ReaderProfile

SALT = 'core.email-verification'
MAX_AGE = 3 * 24 * 60 * 60

VERIFIED = 'verified'
CHANGED = 'changed'
TAKEN = 'taken'
INVALID = 'invalid'


def is_verified(user):
    """Whether this account's current address is confirmed."""
    return not ReaderProfile.objects.filter(user=user, email_verified=False).exists()


def confirmed_holders(email):
    """Accounts that hold `email` as a confirmed address."""
    return (get_user_model()._default_manager.filter(email__iexact=email)
            .exclude(reader_profile__email_verified=False))


def make_token(user, email):
    return signing.dumps({'u': user.pk, 'e': email.strip().lower()}, salt=SALT, compress=True)


def send_link(user, email, request):
    """Mail the confirmation link for `email` to that address."""
    link = notify.site_url(request, reverse('verify_email', args=[make_token(user, email)]))
    body = render_to_string('core/account/verify_email.txt', {
        'user': user, 'email': email, 'link': link, 'days': MAX_AGE // 86400})
    return notify.send('Confirm your email address', body, [email])


def confirm(token):
    """
    Follow a confirmation link. Returns (user or None, outcome).

    The address in the token must still be the account's own, or the one it
    is waiting to move to; and one already confirmed by another account is
    not handed to a second.
    """
    try:
        data = signing.loads(token, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:  # also covers an expired link
        return None, INVALID
    user = get_user_model()._default_manager.filter(pk=data.get('u')).first()
    email = (data.get('e') or '').lower()
    if user is None or not email:
        return None, INVALID

    with transaction.atomic():
        profile = ReaderProfile.for_user(user)
        profile = ReaderProfile.objects.select_for_update().get(pk=profile.pk)
        if confirmed_holders(email).exclude(pk=user.pk).exists():
            return user, TAKEN
        if profile.pending_email and profile.pending_email.lower() == email:
            user.email = profile.pending_email
            user.save(update_fields=['email'])
            profile.pending_email = ''
            profile.email_verified = True
            profile.save(update_fields=['pending_email', 'email_verified'])
            return user, CHANGED
        if user.email.lower() == email:
            if not profile.email_verified:
                profile.email_verified = True
                profile.save(update_fields=['email_verified'])
            return user, VERIFIED
    return user, INVALID


def mark_verified_by_reset(user):
    """
    A password reset link reached this address, which shows it is the owner's.

    Not when another account already holds the address confirmed: two
    confirmed holders would make signing in by address ambiguous.
    """
    if confirmed_holders(user.email).exclude(pk=user.pk).exists():
        return
    ReaderProfile.objects.filter(user=user, email_verified=False).update(email_verified=True)
