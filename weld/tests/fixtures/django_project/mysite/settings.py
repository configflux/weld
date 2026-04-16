"""Django settings for mysite project."""

from django.conf import settings

SECRET_KEY = "fixture-secret-key"
DEBUG = True
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "blog",
    "accounts",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "db.sqlite3",
    }
}
ROOT_URLCONF = "mysite.urls"
