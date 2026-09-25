"""
Telling someone a message arrived.

Two directions. An editor is told when a reader asks for a work, sends one in,
corrects a text or writes to the library, because otherwise those sit in a
queue nobody knows to open. A contributor is told what became of what they
sent, because being asked to help and then hearing nothing is how people stop
helping.

Every send is wrapped. Mail is the one part of a form submission that depends
on a machine we do not run, and a relay that is refusing connections must not
turn "thank you, that has been sent for review" into a 500 on a page where the
reader has just typed out a poem. The record is already saved by the time
anything here runs, so a lost notification costs a prompt, never the work.

Sent while the request is open rather than through the worker: this is the
same trade the password reset already makes, EMAIL_TIMEOUT bounds how long it
can hold the worker for, and a notification that needs Celery to be up is one
that silently stops arriving the day the worker dies.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.urls import reverse

logger = logging.getLogger(__name__)


def staff_recipients():
    """Where library mail goes. Empty means notifications are switched off."""
    return [address for address in getattr(settings, 'LIBRARY_NOTIFY_EMAILS', []) if address]


def site_url(request, path=''):
    """An absolute URL, since a link in an email cannot be relative."""
    if request is not None:
        return request.build_absolute_uri(path)
    base = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
    return f'{base}{path}' if base else path


def send(subject, body, to, reply_to=None):
    """
    One message, with delivery failures logged rather than raised.

    Returns whether it went out, which is what the tests assert on; callers
    ignore it, because there is nothing useful a view can do about a mail
    server being down that it should not already be doing.
    """
    if not to:
        return False

    prefix = getattr(settings, 'EMAIL_SUBJECT_PREFIX_SITE', '[Qasida Library] ')
    try:
        EmailMessage(
            subject=f'{prefix}{subject}',
            body=body,
            # Always our own domain: a relay that is authorised to send for
            # this domain will refuse a message claiming to come from the
            # sender's. Their address goes in Reply-To, so pressing reply in
            # a mail client still writes back to them.
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=list(to),
            reply_to=[reply_to] if reply_to else None,
        ).send(fail_silently=False)
        return True
    except Exception:
        logger.exception('Could not send the notification %r to %s', subject, to)
        return False


# ---------------------------------------------------------------------------
# To the library
# ---------------------------------------------------------------------------

def contribution_received(contribution, request=None):
    """An editor is told a work has been asked for, or sent in."""
    kind = 'submission' if contribution.is_submission else 'request'
    who = contribution.user.username if contribution.user_id else 'someone'
    lines = [
        f'{who} sent a qasida {kind}.',
        '',
        f'Title:        {contribution.title or "—"}',
        f'In script:    {contribution.native_title or "—"}',
        f'Poet:         {contribution.poet_name or "—"}',
        f'In praise of: {contribution.dedication_name or "—"}',
        f'Language:     {contribution.language or "—"}',
    ]
    if contribution.source_url:
        lines.append(f'Source:       {contribution.source_url}')
    if contribution.source_note:
        lines += ['', 'Where it comes from:', contribution.source_note]
    if contribution.note:
        lines += ['', 'Their note:', contribution.note]
    if contribution.is_submission:
        lines += ['', f'The text runs to {len(contribution.lyrics.splitlines())} line(s).']
    lines += [
        '',
        'Review it here:',
        site_url(request, reverse('contribution_inbox')),
    ]

    return send(
        f'Qasida {kind}: {contribution.display_title}',
        '\n'.join(lines),
        staff_recipients(),
        reply_to=contribution.user.email if contribution.user_id else None,
    )


def suggestion_received(suggestion, request=None):
    """An editor is told a reader has corrected a text."""
    changed = ', '.join(change['label'] for change in suggestion.changes())
    if suggestion.suggested_tags:
        changed = f'{changed}, tags' if changed else 'tags'

    lines = [
        f'A correction arrived for "{suggestion.qasida}".',
        '',
        f'Proposes: {changed or "nothing in particular - see the note"}',
    ]
    if suggestion.note:
        lines += ['', 'Their note:', suggestion.note]
    lines += [
        '',
        'The work:',
        site_url(request, suggestion.qasida.get_absolute_url()),
        '',
        'Review it here:',
        site_url(request, reverse('suggestion_inbox')),
    ]

    return send(
        f'Correction: {suggestion.qasida}',
        '\n'.join(lines),
        staff_recipients(),
        reply_to=suggestion.email or None,
    )


def contact_received(message, request=None):
    """An editor is told someone wrote to the library."""
    lines = [
        f'{message.name} <{message.email}> wrote to the library.',
        f'Subject: {message.get_topic_display()}',
    ]
    if message.user_id:
        lines.append(f'Signed in as: {message.user.username}')
    lines += ['', message.message]

    return send(
        f'{message.get_topic_display()} from {message.name}',
        '\n'.join(lines),
        staff_recipients(),
        reply_to=message.email,
    )


# ---------------------------------------------------------------------------
# Back to the person who wrote
# ---------------------------------------------------------------------------

def contribution_decided(contribution, request=None):
    """
    The contributor is told what became of what they sent.

    Sent for an accepted submission as much as for a declined one: the point
    of the message is that someone read it.
    """
    if not (contribution.user_id and contribution.user.email):
        return False
    # An editor's reply goes only to an address its owner has confirmed.
    from .verification import is_verified
    if not is_verified(contribution.user):
        return False

    accepted = contribution.status == contribution.STATUS_ACCEPTED
    what = contribution.display_title
    opening = (f'Thank you - "{what}" has been accepted into the library.'
               if accepted else
               f'"{what}" was not taken into the library this time.')

    lines = [opening]
    if accepted and contribution.published_as_id:
        lines += [
            '',
            'It is being read through before it goes on the site, which is '
            'what happens to every text the library takes in. You will find '
            'it here once it is up:',
            site_url(request, contribution.published_as.get_absolute_url()),
        ]
    if contribution.staff_note:
        lines += ['', 'From the editors:', contribution.staff_note]
    lines += [
        '',
        'Everything you have sent is listed here:',
        site_url(request, reverse('my_contributions')),
    ]

    return send(
        f'Your qasida {"submission" if contribution.is_submission else "request"}: {what}',
        '\n'.join(lines),
        [contribution.user.email],
        reply_to=(staff_recipients() or [None])[0],
    )
