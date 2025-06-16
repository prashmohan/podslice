import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-rehost-placeholder-key-change-me"
DEBUG = True
ALLOWED_HOSTS = ['192.168.68.83', 'localhost', '127.0.0.1']

# Application definition
# --- Add 'rehost_app' to this list ---
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local app for our service's logic
    "rehost_app",
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

ROOT_URLCONF = "podslice_rehost.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "podslice_rehost.wsgi.application"

# --- Modify the DATABASE setting ---
# This points to a single sqlite file in the project's root directory.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "rehost.db",
    }
}

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"

# --- Add these MEDIA settings at the bottom ---
# Media files (User-uploaded content, or in our case, processed audio)
# These settings are crucial for serving the audio files later.
# The MEDIA_ROOT is the absolute path on the filesystem where files are stored.
# As per the design doc, this comes from an environment variable or defaults to /media.
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/media")
# The MEDIA_URL is the URL prefix to serve those files from.
MEDIA_URL = "/audio/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"