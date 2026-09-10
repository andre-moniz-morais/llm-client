"""Settings for CRAFT."""

from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or get_random_secret_key()
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "apps.common",
    "apps.accounts",
    "apps.catalog",
    "apps.chat",
    "apps.generation",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.common.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

# Persistent connections are worth having behind a process manager; they are
# pointless against SQLite, so only the PostgreSQL branches below set them.
DB_CONN_MAX_AGE = int(os.environ.get("DJANGO_DB_CONN_MAX_AGE", "60"))


def _database_config() -> dict:
    """SQLite for a checkout, PostgreSQL for a deployment.

    Configured either through a single ``DATABASE_URL`` - what most hosts and
    container images hand you - or through discrete ``DJANGO_DB_*`` variables,
    which are easier to point at an existing Postgres container. Neither set
    means SQLite, so the app still runs straight from a clone.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        parts = urlparse(url)
        if parts.scheme not in {"postgres", "postgresql", "psql"}:
            raise ValueError(f"Unsupported DATABASE_URL scheme: {parts.scheme!r}")
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parts.path.lstrip("/")),
            "USER": unquote(parts.username or ""),
            "PASSWORD": unquote(parts.password or ""),
            "HOST": parts.hostname or "",
            "PORT": str(parts.port or ""),
            "CONN_MAX_AGE": DB_CONN_MAX_AGE,
            "CONN_HEALTH_CHECKS": True,
        }

    if os.environ.get("DJANGO_DB_HOST"):
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DJANGO_DB_NAME", "craft"),
            "USER": os.environ.get("DJANGO_DB_USER", "craft"),
            "PASSWORD": os.environ.get("DJANGO_DB_PASSWORD", ""),
            "HOST": os.environ["DJANGO_DB_HOST"],
            "PORT": os.environ.get("DJANGO_DB_PORT", "5432"),
            "CONN_MAX_AGE": DB_CONN_MAX_AGE,
            "CONN_HEALTH_CHECKS": True,
        }

    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / os.environ.get("DJANGO_DB_NAME", "db.sqlite3"),
    }


DATABASES = {"default": _database_config()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / os.environ.get("DJANGO_MEDIA_ROOT", "media")

# The manifest backend rewrites asset URLs with a content hash, which needs a
# `collectstatic` pass to exist first. Keep it opt-in so the app runs (and its
# tests pass) straight from a checkout, and turn it on for deployments.
STATIC_BACKEND = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
    if env_bool("DJANGO_STATIC_MANIFEST", False)
    else "whitenoise.storage.CompressedStaticFilesStorage"
)

# Generated media goes to an S3 bucket when one is configured, and to the local
# filesystem otherwise.  `AWS_S3_ENDPOINT_URL` is what points this at MinIO (or
# any other S3-compatible server) instead of Amazon.
AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME", "")
USE_S3 = bool(AWS_STORAGE_BUCKET_NAME)

if USE_S3:
    _s3_options = {
        "bucket_name": AWS_STORAGE_BUCKET_NAME,
        "region_name": os.environ.get("AWS_S3_REGION_NAME", "us-east-1"),
        "default_acl": os.environ.get("AWS_DEFAULT_ACL") or None,
        "file_overwrite": False,
        "location": os.environ.get("AWS_LOCATION", "media"),
        # Signed URLs by default: a private bucket is the safe assumption, and
        # generated media is per-user.  Set AWS_QUERYSTRING_AUTH=false when the
        # bucket (or the CDN in front of it) is meant to be publicly readable.
        "querystring_auth": env_bool("AWS_QUERYSTRING_AUTH", True),
        "querystring_expire": int(os.environ.get("AWS_QUERYSTRING_EXPIRE", "3600")),
        # MinIO does not do virtual-host style addressing without DNS per
        # bucket, so path style is the portable choice.
        "addressing_style": os.environ.get("AWS_S3_ADDRESSING_STYLE", "path"),
    }
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        _s3_options["access_key"] = os.environ["AWS_ACCESS_KEY_ID"]
    if os.environ.get("AWS_SECRET_ACCESS_KEY"):
        _s3_options["secret_key"] = os.environ["AWS_SECRET_ACCESS_KEY"]
    if os.environ.get("AWS_S3_ENDPOINT_URL"):
        _s3_options["endpoint_url"] = os.environ["AWS_S3_ENDPOINT_URL"]
    # The host browsers should fetch from, when it differs from the endpoint the
    # app writes through - an internal MinIO address versus a public one.
    if os.environ.get("AWS_S3_CUSTOM_DOMAIN"):
        _s3_options["custom_domain"] = os.environ["AWS_S3_CUSTOM_DOMAIN"]

    DEFAULT_STORAGE = {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": _s3_options,
    }
else:
    DEFAULT_STORAGE = {"BACKEND": "django.core.files.storage.FileSystemStorage"}

STORAGES = {
    "default": DEFAULT_STORAGE,
    "staticfiles": {"BACKEND": STATIC_BACKEND},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "chat:index"
LOGOUT_REDIRECT_URL = "accounts:login"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "craft",
        "OPTIONS": {"MAX_ENTRIES": 5000},
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

# --------------------------------------------------------------------------- #
# Model provider
# --------------------------------------------------------------------------- #

KIE_API_BASE = os.environ.get("KIE_API_BASE", "https://api.kie.ai")
KIE_UPLOAD_BASE = os.environ.get("KIE_UPLOAD_BASE", "https://kieai.redpandaai.co")
KIE_REQUEST_TIMEOUT = int(os.environ.get("KIE_REQUEST_TIMEOUT", "120"))

# How long a generation may stay queued before the UI stops polling it.
GENERATION_TIMEOUT_SECONDS = int(os.environ.get("GENERATION_TIMEOUT_SECONDS", "900"))

# Catalog freshness.  The index is one small request so it refreshes often;
# per-model schemas change rarely and are much larger.
CATALOG_INDEX_TTL = int(os.environ.get("CATALOG_INDEX_TTL", str(60 * 60)))
CATALOG_SPEC_TTL = int(os.environ.get("CATALOG_SPEC_TTL", str(24 * 60 * 60)))

# Fernet key used to encrypt stored API keys at rest.  Generated per install if
# unset, which means stored keys become unreadable when the process restarts -
# fine for a local trial, not for a deployment.
CREDENTIALS_ENCRYPTION_KEY = os.environ.get("CREDENTIALS_ENCRYPTION_KEY", "")

TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")

# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #

# Behind a reverse proxy that terminates TLS, Django sees a plain HTTP request
# and only learns the original scheme from a forwarded header.  Without this,
# SECURE_SSL_REDIRECT below redirects forever.  Only trust these headers when a
# proxy you control is actually setting them.
if env_bool("DJANGO_TRUST_PROXY_HEADERS", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # The container healthcheck talks plain HTTP to the port, so it must not be
    # answered with a redirect to HTTPS.
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
