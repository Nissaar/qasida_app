#!/bin/sh

# wait for postgres to be ready
echo "Waiting for PostgreSQL..."
sleep 5

# apply migrations
#
# Only `migrate` runs here. `makemigrations` on a server would invent a
# migration that exists nowhere in git the moment the code and database
# disagree, leaving production with history the repository has never seen.
# Migrations are authored on a developer's machine and committed.
echo "Applying database migrations..."
python manage.py migrate --noinput

# the crawl sources are declared in core/sources.py, so a fresh deployment
# starts with the full set rather than an empty table
echo "Ensuring default crawl sources..."
python manage.py ensure_sources

# whitenoise serves from STATIC_ROOT, which does not exist until this runs
echo "Collecting static files..."
python manage.py collectstatic --noinput

# execute the passed command (e.g., gunicorn)
exec "$@"
