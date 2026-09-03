#!/bin/sh

# wait for postgres to be ready
echo "Waiting for PostgreSQL..."
sleep 5

# apply migrations
echo "Applying database migrations..."
python manage.py makemigrations
python manage.py migrate

# the crawl sources are declared in core/sources.py, so a fresh deployment
# starts with the full set rather than an empty table
echo "Ensuring default crawl sources..."
python manage.py ensure_sources

# whitenoise serves from STATIC_ROOT, which does not exist until this runs
echo "Collecting static files..."
python manage.py collectstatic --noinput

# execute the passed command (e.g., gunicorn)
exec "$@"
