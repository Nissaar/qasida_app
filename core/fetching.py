"""
Polite HTTP fetching for the crawlers.

Sites in this collection are small and some rate-limit hard (qasidacollection
answers 429 immediately when hit without a pause). A bare requests.get loses
those pages, so every crawler request goes through here: one request at a time
per host with a minimum gap, exponential backoff on the statuses that mean
"slow down", and Retry-After honoured when the server sends it.
"""

import random
import threading
import time
from urllib.parse import urlsplit

import requests

USER_AGENT = 'QasidaAppBot/1.0 (+http://your-app-domain.com)'
HEADERS = {'User-Agent': USER_AGENT}

# Minimum seconds between requests to the same host.
DEFAULT_HOST_DELAY = 1.5
# Hosts known to need a wider gap.
HOST_DELAYS = {
    'www.qasidacollection.com': 6.0,
    'qasidacollection.com': 6.0,
    # The archive throttles hard and serves 503s under load; it needs a wider
    # gap than an ordinary site.
    'web.archive.org': 4.0,
    'archive.org': 4.0,
}

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 5
# Cap so a hostile Retry-After cannot park the crawl for an hour.
MAX_BACKOFF = 60.0

_last_request = {}
_lock = threading.Lock()


class RateLimited(requests.RequestException):
    """Raised when a host kept refusing after every attempt."""


class BotChallenge(requests.RequestException):
    """
    The host served a JavaScript bot challenge rather than the page.

    These cannot be satisfied by a plain HTTP client, and retrying only wastes
    the backoff schedule, so this is raised immediately. Getting past one is a
    matter for the site's owner - allowlisting the crawler or providing an
    export - not for the crawler.
    """


# Markers that identify an interstitial challenge page instead of content.
CHALLENGE_MARKERS = (
    'vercel security checkpoint',
    'enable javascript to continue',
    'checking your browser before accessing',
    'cf-browser-verification',
    'just a moment...',
    'attention required! | cloudflare',
)


def _is_challenge_page(response):
    """True when the body is a bot challenge rather than the resource."""
    content_type = response.headers.get('Content-Type', '')
    if 'html' not in content_type.lower():
        return False
    # Only the head of the document is needed, and decoding a whole page here
    # would be wasteful.
    head = response.text[:4000].lower()
    return any(marker in head for marker in CHALLENGE_MARKERS)


def _host_delay(host):
    return HOST_DELAYS.get(host, DEFAULT_HOST_DELAY)


def _wait_turn(host):
    """Space out requests to one host without blocking others."""
    with _lock:
        delay = _host_delay(host)
        previous = _last_request.get(host)
        now = time.monotonic()
        if previous is not None:
            gap = now - previous
            if gap < delay:
                time.sleep(delay - gap)
                now = time.monotonic()
        _last_request[host] = now


def _retry_after_seconds(response, attempt):
    """Prefer the server's own figure, else back off exponentially with jitter."""
    header = response.headers.get('Retry-After')
    if header:
        try:
            return min(float(header), MAX_BACKOFF)
        except ValueError:
            pass  # HTTP-date form; fall through to our own schedule
    return min(MAX_BACKOFF, (2 ** attempt) + random.uniform(0, 0.75))


def polite_get(url, timeout=30, headers=None, allow=(), **kwargs):
    """
    GET a URL, pausing between requests to the same host and retrying the
    statuses that mean "too fast". Raises on a final failure so callers can
    record the source as failed and move on.

    `allow` lists status codes to hand back untouched instead of raising - the
    damas pager, for instance, reads a 400 as "past the last page".
    """
    host = urlsplit(url).netloc
    merged = dict(HEADERS)
    if headers:
        merged.update(headers)

    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        _wait_turn(host)
        try:
            response = requests.get(url, timeout=timeout, headers=merged, **kwargs)
        except requests.RequestException as e:
            last_error = e
            time.sleep(min(MAX_BACKOFF, 2 ** attempt))
            continue

        if _is_challenge_page(response):
            raise BotChallenge(
                f"{host} served a JavaScript bot challenge (HTTP "
                f"{response.status_code}). A plain HTTP client cannot pass it; "
                f"ask the site owner to allowlist the crawler or supply an export.")

        if response.status_code in allow:
            return response

        if response.status_code in RETRY_STATUSES:
            pause = _retry_after_seconds(response, attempt)
            last_error = requests.HTTPError(
                f"{response.status_code} from {host}", response=response)
            if attempt < MAX_ATTEMPTS - 1:
                print(f"    {response.status_code} from {host}; waiting {pause:.1f}s "
                      f"(attempt {attempt + 1}/{MAX_ATTEMPTS})")
                time.sleep(pause)
                continue
            raise RateLimited(
                f"{host} still returning {response.status_code} after "
                f"{MAX_ATTEMPTS} attempts") from last_error

        response.raise_for_status()
        return response

    raise RateLimited(f"{host} unreachable after {MAX_ATTEMPTS} attempts") from last_error
