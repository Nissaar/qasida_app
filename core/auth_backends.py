"""Signing in with a username or an email address."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class UsernameOrEmailBackend(ModelBackend):
    """
    Accept either identifier in the one sign-in field.

    Django's backend only knows usernames, and people remember whichever of
    the two they typed last. Both lookups are case-insensitive, because nobody
    recalls how they capitalised an address.

    Registration refuses an address that is already in use, but the column
    carries no unique constraint and accounts created before that rule - or by
    createsuperuser, which never asks - can still collide. An identifier that
    matches more than one account is therefore refused rather than guessed at:
    signing someone into the wrong account is far worse than asking them to
    use their username instead. Django's own backend stays configured behind
    this one, so an exact username always remains a way in.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        if username is None:
            username = kwargs.get(user_model.USERNAME_FIELD)
        if not username or password is None:
            return None

        candidates = list(user_model._default_manager.filter(
            Q(username__iexact=username) | Q(email__iexact=username)))

        # An exact username wins outright: it is unique by constraint, so it
        # cannot be the ambiguous case even if an address happens to match too.
        exact = [user for user in candidates if user.get_username() == username]
        if len(exact) == 1:
            candidates = exact

        if len(candidates) != 1:
            # Hash anyway, so a missing or ambiguous identifier takes the same
            # time as a wrong password and cannot be told apart by timing.
            user_model().set_password(password)
            return None

        user = candidates[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
