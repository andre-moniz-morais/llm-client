# Production image.  Slim rather than Alpine: the wheels for psycopg, Pillow and
# cryptography are prebuilt for glibc, so this both builds and starts faster.
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libpq for psycopg, and the image libraries Pillow links against at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        libjpeg62-turbo \
        libwebp7 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# A checkout that lost the executable bit (Windows, a zip export) would
# otherwise fail at start with a confusing permission error.
RUN chmod +x /app/scripts/entrypoint.sh

# Hashed asset URLs, baked in at build time so startup does no filesystem work
# and every replica serves identical URLs.  The runtime needs the same flag for
# the storage backend to read the manifest, hence ENV rather than a build arg.
ENV DJANGO_STATIC_MANIFEST=true
RUN DJANGO_DEBUG=false \
    DJANGO_SECRET_KEY=build-time-only \
    python manage.py collectstatic --noinput --clear

# Drop privileges. Done after collectstatic so the build can write staticfiles.
RUN useradd --create-home --uid 10001 craft \
    && mkdir -p /app/media \
    && chown -R craft:craft /app/media
USER craft

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS -H "Host: localhost" http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
