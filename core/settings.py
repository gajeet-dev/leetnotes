import logging.config
from pathlib import Path

import environ

from .logging import configure_logging

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, True),  # local-only project
    LOG_LEVEL=(str, "INFO"),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env(
    "SECRET_KEY",
    default="django-insecure-ttf$s^z6dyc^n%h8m02%wvzx-#z-h&zgp-s3(q#=8&g1@1y!-n",
)

DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

LOGGING_CONFIG = None  # stop Django applying its default logging config
LOGGING = configure_logging(debug=DEBUG, level=env("LOG_LEVEL"))
logging.config.dictConfig(LOGGING)

LEETCODE_SESSION = env("LEETCODE_SESSION")  # no default: required
LEETCODE_CSRF_TOKEN = env("LEETCODE_CSRF_TOKEN")  # no default: required
LEETCODE_USERNAME = env("LEETCODE_USERNAME")  # no default: required
LEETCODE_BASE_URL = env("LEETCODE_BASE_URL", default="https://leetcode.com")

# applied on http session
LEETCODE_REQUESTS_PER_SECOND = env.float("LEETCODE_REQUESTS_PER_SECOND", default=1.0)
LEETCODE_REQUESTS_PER_MINUTE = env.float("LEETCODE_REQUESTS_PER_MINUTE", default=20.0)

# applied on process_pending()
LEETCODE_PROBLEMS_PER_MINUTE = env.float("LEETCODE_PROBLEMS_PER_MINUTE", default=5.0)

NOTES_OUTPUT_DIRS = [
    Path(p.strip()).expanduser()
    for p in env.list("NOTES_OUTPUT_DIRS", default=[str(BASE_DIR / "notes_output")])
    if p.strip()
]

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "problems",
    "leetcode",
    "notes",
    "prefill",
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

ROOT_URLCONF = "core.urls"

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

WSGI_APPLICATION = "core.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = env("TIME_ZONE", default="UTC")

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = "static/"

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}
