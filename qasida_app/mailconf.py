"""
Working out mail settings from the environment.

Plain functions with no Django imports, so settings.py can call them while it
is still being built, and so the rules can be tested directly rather than by
re-importing settings under a different environment.
"""

# Mailgun runs two independent regions. A domain created in one is not
# reachable through the other's SMTP host, and the failure presents as a
# rejected login rather than as anything mentioning regions.
MAILGUN_HOSTS = {
    'us': 'smtp.mailgun.org',
    'eu': 'smtp.eu.mailgun.org',
}
DEFAULT_MAILGUN_REGION = 'us'

# 587 with STARTTLS is Mailgun's own recommendation. 465 is there for networks
# that block it, and needs implicit TLS instead of STARTTLS.
DEFAULT_SMTP_PORT = 587
IMPLICIT_TLS_PORT = 465

SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
CONSOLE_BACKEND = 'django.core.mail.backends.console.EmailBackend'


def env_int(raw, default):
    """
    An unset or unparseable value falls back instead of killing the process.

    Compose writes an empty string for a variable that is declared in the
    compose file but left blank in .env, and int("") raises, which would take
    the whole site down over a mail setting.
    """
    try:
        return int((raw or '').strip() or default)
    except (TypeError, ValueError, AttributeError):
        return default


def env_flag(raw, default):
    """Accept the spellings people actually write, not only "True"."""
    text = (raw or '').strip().lower()
    if not text:
        return default
    return text in ('1', 'true', 'yes', 'on')


def mailgun_config(login, password, region=None, port_raw=None):
    """
    The EMAIL_* settings Mailgun needs, or {} when it is not configured.

    Mailgun is an ordinary SMTP relay, so the generic DJANGO_EMAIL_* variables
    drive it perfectly well. This exists so that is not something anyone has to
    work out: give it the SMTP credentials shown on the Mailgun dashboard for a
    sending domain, and the host, port, backend and TLS all follow.
    """
    login = (login or '').strip()
    password = (password or '').strip()
    if not (login and password):
        return {}

    region = (region or DEFAULT_MAILGUN_REGION).strip().lower()
    port = env_int(port_raw, DEFAULT_SMTP_PORT)
    implicit_tls = port == IMPLICIT_TLS_PORT

    return {
        'EMAIL_BACKEND': SMTP_BACKEND,
        'EMAIL_HOST': MAILGUN_HOSTS.get(region, MAILGUN_HOSTS[DEFAULT_MAILGUN_REGION]),
        'EMAIL_PORT': port,
        'EMAIL_HOST_USER': login,
        'EMAIL_HOST_PASSWORD': password,
        'EMAIL_USE_SSL': implicit_tls,
        'EMAIL_USE_TLS': not implicit_tls,
    }


def looks_like_an_api_key(password):
    """
    Whether a password looks like a Mailgun API key rather than an SMTP one.

    These are different credentials, and an API key will not authenticate over
    SMTP. It is the commonest way to configure Mailgun wrongly, and it fails
    only at send time, so from the reader's side a password reset simply never
    arrives and nothing says why.
    """
    text = (password or '').strip().lower()
    if not text:
        return False
    if text.startswith('key-'):
        return True
    # Newer private keys are long unbroken hex.
    return len(text) >= 32 and all(c in '0123456789abcdef-' for c in text)
