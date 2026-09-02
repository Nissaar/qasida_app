#!/bin/sh

# wait for postgres to be ready
echo "Waiting for PostgreSQL..."
python -c "
import socket
import time
import os

host = os.environ.get('DB_HOST', 'db')
port = int(os.environ.get('DB_PORT', 5432))

print(f'Waiting for {host}:{port}...')
start_time = time.time()
while time.time() - start_time < 30:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect((host, port))
            print('PostgreSQL is up!')
            break
    except OSError:
        time.sleep(1)
else:
    print('Timeout waiting for PostgreSQL.')
"

# apply migrations
echo "Applying database migrations..."
python manage.py makemigrations
python manage.py migrate

# execute the passed command (e.g., gunicorn)
exec "$@"
