"""Django settings. Everything that differs between environments comes from environment variables."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY when DJANGO_DEBUG is off.")
    SECRET_KEY = "django-insecure-local-development-only"
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

# Behind TLS (directly or via a proxy that sets X-Forwarded-Proto): HTTPS only, secure cookies, HSTS.
if env_bool("DJANGO_HTTPS", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "3600"))
    # HSTS for subdomains and the preload list are decisions for the whole domain, not for this app.
    SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "stations",
    "planner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if not DEBUG:
    # Serves collected static files (admin, browsable API) without a separate web server.
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

# Routes are cached so a repeated trip costs no routing call. The file cache is shared by every
# worker process without extra infrastructure; set REDIS_URL to use Redis instead.
if env("REDIS_URL"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": env("REDIS_URL")}}
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": env("CACHE_DIR", str(BASE_DIR / ".cache")),
            "OPTIONS": {"MAX_ENTRIES": 5000},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "EXCEPTION_HANDLER": "planner.exceptions.api_exception_handler",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

# --- Trip planning -------------------------------------------------------------------------------

VEHICLE_MAX_RANGE_MILES = float(env("VEHICLE_MAX_RANGE_MILES", "500"))
VEHICLE_MPG = float(env("VEHICLE_MPG", "10"))
# Stations farther than this from the route line are not considered.
ROUTE_CORRIDOR_MILES = float(env("ROUTE_CORRIDOR_MILES", "5"))
# Fixed cost charged per stop when choosing stops, so the plan does not stop for a few cents.
# It steers the choice only; reported costs are fuel alone. 0 gives the pure cheapest plan.
FUEL_STOP_PENALTY_USD = float(env("FUEL_STOP_PENALTY_USD", "5"))

# Providers are tried in order; the next one is used only if the previous one is unreachable.
ROUTING_PROVIDERS = env_list("ROUTING_PROVIDERS", "osrm,valhalla")
OSRM_BASE_URL = env("OSRM_BASE_URL", "https://router.project-osrm.org")
VALHALLA_BASE_URL = env("VALHALLA_BASE_URL", "https://valhalla1.openstreetmap.de")
NOMINATIM_BASE_URL = env("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")
OUTBOUND_TIMEOUT_SECONDS = float(env("OUTBOUND_TIMEOUT_SECONDS", "15"))
# The public OSM services ask for an identifying User-Agent and at most one request per second.
OUTBOUND_USER_AGENT = env("OUTBOUND_USER_AGENT", "fuel-route-api/1.0 (+https://github.com/vpetreski/fuel-route-api)")
OUTBOUND_MIN_INTERVAL_SECONDS = float(env("OUTBOUND_MIN_INTERVAL_SECONDS", "1"))
ROUTE_CACHE_SECONDS = int(env("ROUTE_CACHE_SECONDS", str(7 * 24 * 3600)))
GEOCODE_CACHE_SECONDS = int(env("GEOCODE_CACHE_SECONDS", str(30 * 24 * 3600)))
