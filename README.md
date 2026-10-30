# Qasida App

A searchable library of qasidas, naats and other devotional poetry. It collects texts from public sites, repairs and cleans them (Arabic-script fixes, OCR of scanned pages, verse markers), pairs originals with transliterations and translations, and serves them as a website with search, collections, favourites and a reading history.

## What it does

- **Crawls** a list of source sites politely (robots.txt, rate limits, honest user agent) with Celery; the default list is in [core/sources.py](core/sources.py) and is reconciled into the database at start (`manage.py ensure_sources`). Pausing a site in the admin sticks across restarts.
- **Cleans and enriches**: de-duplicates (`find_duplicates`), repairs shattered Arabic text from PDFs, OCRs scanned pages (`ocr_scans`), detects collections, translates missing texts offline (Argos Translate, so Urdu and Persian work without the internet).
- **Serves** the library with full-text search, poets, tags, collections, favourites, reading history, reader profiles, user suggestions and contributions, and a contact form. Django admin for curation; XML sitemaps for search engines.

## Stack

Django 6.1, PostgreSQL 15, Redis, Celery (worker + beat), gunicorn behind nginx / Traefik, WhiteNoise for static files.

## Run it locally

```sh
docker compose up --build        # db, redis, web on :8000, celery worker and beat
docker compose exec web python manage.py createsuperuser
```
The services use host networking on purpose: under WSL2's mirrored networking mode Docker bridge networks cannot reach the internet, and the crawlers need outbound HTTPS (explained in [docker-compose.yml](docker-compose.yml)). The values in that file (`development-only-not-secret`, the database password) are for local work only.

## Deploy

[.env.prod.example](.env.prod.example) lists every setting for production: the hostname Traefik routes, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, a fresh `DJANGO_SECRET_KEY`, the database login, and password-reset mail (Mailgun SMTP credentials, not the account API key). Copy it to `.env`, which is git-ignored. The app refuses to start in production without a real secret key. The container entrypoint waits for Postgres, runs migrations and the source reconciliation, and stops on any failure instead of serving a half-migrated site.

## Tests

```sh
python manage.py test core --noinput
python manage.py makemigrations --check --dry-run      # a model change needs its migration
```
Both run in CI ([.github/workflows/tests.yml](.github/workflows/tests.yml)); a test report is built from the results.

## Management commands

`ensure_sources`, `find_duplicates`, `detect_collections`, `ocr_scans`, `repair_arabic`, `reflow_lyrics`, `tidy_lyrics`, `tidy_verse_markers`, `split_translations`, `translate_missing`, `backfill_search`: run with `python manage.py <name> --help`.

## Layout

| Path | What |
|---|---|
| `core/` | the Django app: models, crawling tasks, extraction, search, templates, tests |
| `qasida_app/` | settings, URLs, Celery and WSGI setup |
| `templates/` | project-level templates (admin overrides) |
| `nginx/media.conf` | nginx settings for serving uploaded media |
| `entrypoint.sh` | container start-up: wait for the database, migrate, reconcile sources |
