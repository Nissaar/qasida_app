#!/bin/sh
#
# Start-up for the application container.
#
# Every step here must either succeed or stop the container. A container that
# carries on after a failed migration serves a site whose schema does not match
# its code, which shows up as intermittent 500s on whichever pages happen to
# touch the changed tables - and with the old fixed `sleep 5` wait for
# Postgres, a database that took six seconds to accept connections produced
# exactly that, silently.
set -e

echo "Waiting for PostgreSQL..."
# Ask the database itself rather than guessing at a duration. A slow disk, a
# cold volume or a busy host all move that number, and there is no value that
# is both short enough not to waste a restart and long enough to be safe.
python - <<'PY'
import os, sys, time

# A bare interpreter is not manage.py, so it has to be told where the settings
# live before anything touches them.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'qasida_app.settings')

import django
from django.db import connections
from django.db.utils import OperationalError

django.setup()

DEADLINE = 60
started = time.monotonic()
while True:
    try:
        connections['default'].ensure_connection()
        waited = time.monotonic() - started
        print(f"PostgreSQL is accepting connections after {waited:.1f}s.")
        break
    except OperationalError as exc:
        if time.monotonic() - started > DEADLINE:
            sys.exit(f"PostgreSQL was still unreachable after {DEADLINE}s: {exc}")
        time.sleep(1)
PY

# Only `migrate` runs here. `makemigrations` on a server would invent a
# migration that exists nowhere in git the moment the code and database
# disagree, leaving production with history the repository has never seen.
# Migrations are authored on a developer's machine and committed.
echo "Applying database migrations..."
python manage.py migrate --noinput

# Belt and braces. `migrate` exiting zero with work still outstanding should be
# impossible, but the failure it would cause is invisible and confusing, so it
# is worth one cheap check rather than a week of hunting intermittent 500s.
echo "Verifying no migrations remain..."
python manage.py migrate --check --noinput

# the crawl sources are declared in core/sources.py, so a fresh deployment
# starts with the full set rather than an empty table
echo "Ensuring default crawl sources..."
python manage.py ensure_sources

# whitenoise serves from STATIC_ROOT, which does not exist until this runs
echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Start-up checks passed; handing over to: $*"
exec "$@"
