"""
A cap on how often one person can post the same kind of thing.

The contact form and the contribution forms are the only places on this site
where a stranger, or a fresh account, can put text into a queue an editor has
to read. Without a bound, one afternoon of scripted posting is enough to bury
a queue that a volunteer works through by hand.

Counted in the cache, and every call is wrapped: if Redis is away the site
keeps accepting messages, it simply stops counting. That is the right way
round - a library that refuses to hear from anyone because a cache is down has
failed worse than one that accepts a few too many.

The same shape as the sign-in counters in core.account_views, which came
first; kept here because these are used from two modules and are worth
testing on their own.
"""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


def client_ip(request):
    """The visitor's address, trusting the proxy header only behind a proxy."""
    from django.conf import settings
    if getattr(settings, 'USE_X_FORWARDED_HOST', False):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def over_limit(key, limit):
    """Whether `key` has already been used its allowance of times."""
    try:
        return (cache.get(key) or 0) >= limit
    except Exception:
        logger.warning('Rate limit for %s could not be read; allowing.', key)
        return False


def record(key, window):
    """Count one use of `key`, expiring `window` seconds after the first."""
    try:
        # add() then incr(): incr on a missing key raises, and add on its own
        # would push the window forward with every post, so a steady trickle
        # would never expire.
        cache.add(key, 0, window)
        cache.incr(key)
    except Exception:
        logger.warning('Rate limit for %s could not be recorded.', key)


def keys_for(request, name):
    """
    The counters one post touches: the account, and the address it came from.

    Both, because either alone is easy to get around - a new account from the
    same machine, or one account through a proxy pool - and because a library
    served in mosques and households shares addresses, so the per-address
    allowance is set far looser than the per-account one by the caller.
    """
    keys = [f'{name}:ip:{client_ip(request)}']
    user = getattr(request, 'user', None)
    if getattr(user, 'is_authenticated', False):
        keys.append(f'{name}:user:{user.pk}')
    return keys
