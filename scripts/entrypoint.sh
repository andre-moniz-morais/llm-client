#!/bin/sh
# Container entrypoint: bring the schema up to date, then serve.
set -eu

# The database lives in a container this compose file does not own, so it may
# still be starting up. Give it a bounded number of tries rather than crash-
# looping the app container.
if [ "${CRAFT_WAIT_FOR_DB:-true}" = "true" ]; then
    attempt=1
    until python manage.py check --database default >/dev/null 2>&1; do
        if [ "$attempt" -ge "${CRAFT_DB_WAIT_TRIES:-30}" ]; then
            echo "Database not reachable after $attempt attempts; giving up." >&2
            python manage.py check --database default   # print the real error
            exit 1
        fi
        echo "Waiting for the database (attempt $attempt)..."
        attempt=$((attempt + 1))
        sleep 2
    done
fi

if [ "${CRAFT_MIGRATE_ON_START:-true}" = "true" ]; then
    echo "Applying migrations..."
    python manage.py migrate --noinput
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
