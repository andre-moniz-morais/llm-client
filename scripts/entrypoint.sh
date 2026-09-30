#!/bin/sh
# Container entrypoint: bring the schema up to date, then serve.
#
# With arguments it runs those instead of the web server, after the same
# database wait - that is how the worker and beat containers start from this
# image (see docker-compose.prod.yml).
set -eu

# Checked before anything else: settings refuse to load without it, and the
# resulting error would otherwise look like a database problem.
if [ "${DJANGO_DEBUG:-true}" = "false" ] && [ -z "${CREDENTIALS_ENCRYPTION_KEY:-}" ]; then
    echo "CREDENTIALS_ENCRYPTION_KEY is not set. Generate one with:" >&2
    echo "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"" >&2
    exit 1
fi

# The database lives in a container this compose file does not own, so it may
# still be starting up. Give it a bounded number of tries rather than crash-
# looping the app container; each failed try logs the driver's reason.
if [ "${CRAFT_WAIT_FOR_DB:-true}" = "true" ]; then
    python manage.py wait_for_db --tries "${CRAFT_DB_WAIT_TRIES:-30}"
fi

if [ "${CRAFT_MIGRATE_ON_START:-true}" = "true" ]; then
    echo "Applying migrations..."
    python manage.py migrate --noinput
fi

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Model calls are synchronous and can legitimately run for minutes, so the
# worker timeout has to sit above KIE_REQUEST_TIMEOUT or gunicorn kills a
# healthy request. Threads rather than extra processes keep memory flat while
# those requests are just waiting on the network.
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-3}" \
    --worker-class gthread \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout "${GUNICORN_TIMEOUT:-180}" \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    --forwarded-allow-ips '*'
