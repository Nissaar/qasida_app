"""
Who a page was rendered for, so an offline copy is only shown back to them.

Pages are kept by the service worker so the library stays readable without a
connection, and a page rendered while signed in carries that reader's name,
their reading history and saved works, or - for staff - works still awaiting
review. Emptying that cache when someone presses "sign out" is not enough:
closing the browser, an expired session, the admin's own sign-out or a
deleted account all end a session without passing through that page, and the
next person to open the browser offline was shown the last one's library.

Every page therefore says whom it was made for, as an opaque id in a
response header and in the page itself, and the same id is kept in a cookie
that lives exactly as long as the session:

- the service worker empties its page cache whenever the id on a fresh page
  differs from the last one it saw, which covers every change of user online;
- a cached page checks, before it draws anything, that the id it was made
  for is still the one in the cookie, which covers the browser having been
  closed or the session having run out while offline.

The id reveals nothing: it is a keyed hash of the account number, the same
on every device and useless to anyone without the site's secret key.
"""

from django.conf import settings
from django.utils.crypto import salted_hmac

COOKIE = 'qasida_viewer'
HEADER = 'X-Qasida-Viewer'
ANONYMOUS = 'anon'


def viewer_id(user):
    """The opaque id of whoever this is, or ANONYMOUS."""
    if not getattr(user, 'is_authenticated', False):
        return ANONYMOUS
    return salted_hmac('core.viewer', str(user.pk)).hexdigest()[:20]


def context(request):
    """Makes the id available to the page shell."""
    return {'viewer_id': viewer_id(getattr(request, 'user', None)),
            'viewer_cookie': COOKIE}


class ViewerMiddleware:
    """Stamps each page with its viewer, and keeps the cookie in step."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Read after the view has run: signing in or out changes it.
        viewer = viewer_id(getattr(request, 'user', None))
        if 'text/html' in response.get('Content-Type', ''):
            response[HEADER] = viewer

        current = request.COOKIES.get(COOKIE)
        if viewer == ANONYMOUS:
            if current:
                response.delete_cookie(COOKIE, samesite='Lax')
        elif current != viewer:
            session = getattr(request, 'session', None)
            # Ends when the session does: with the browser, when "keep me
            # signed in" was left unticked, and otherwise with the session's
            # own expiry.
            max_age = None
            if session is not None and not session.get_expire_at_browser_close():
                max_age = session.get_expiry_age()
            response.set_cookie(COOKIE, viewer, max_age=max_age, samesite='Lax',
                                secure=settings.SESSION_COOKIE_SECURE, httponly=False)
        return response
