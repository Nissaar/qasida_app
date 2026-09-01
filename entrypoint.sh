#!/bin/sh

# wait for postgres to be ready
echo "Waiting for PostgreSQL..."
sleep 5

# apply migrations
echo "Applying database migrations..."
python manage.py makemigrations
python manage.py migrate

# execute the passed command (e.g., gunicorn)
exec "$@"
