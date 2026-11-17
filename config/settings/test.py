import os

os.environ.setdefault("DJANGO_SECRET_KEY", "solo-per-i-test")
os.environ.setdefault("DATABASE_URL", "postgres://dumetodo:dumetodo@localhost:5432/dumetodo")

from .base import *

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_BROKER_URL = "memory://"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
