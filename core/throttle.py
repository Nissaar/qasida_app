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

The sign-in counters in core.account_views count through these helpers too,
so there is one idea of who a visitor is and one way of counting them.
"""

import ipaddress
import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def client_ip(request):
    """
    The visitor's address, as the proxies in front of us saw it.

    Each proxy appends the address it received the request from to
    X-Forwarded-For, so the entry `TRUSTED_PROXY_COUNT` places from the right
    is the one our own outermost proxy wrote. Everything to the left of it
    arrived from the visitor and is whatever they chose to send: reading the
    leftmost entry, as this used to, let anyone claim a fresh address on every
    request and walk straight past every limit here.
    """
    trusted = getattr(settings, 'TRUSTED_PROXY_COUNT', 0)
    remote = request.META.get('REMOTE_ADDR', '')
    if not trusted:
        return remote
    hops = [hop.strip() for hop in
            request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if hop.strip()]
    if len(hops) < trusted:
        return remote
    candidate = hops[-trusted]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return remote
    return candidate


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


def clear(key):
    """Forget the count for `key`, as after a successful sign-in."""
    try:
        cache.delete(key)
    except Exception:
        logger.warning('Rate limit for %s could not be cleared.', key)


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
