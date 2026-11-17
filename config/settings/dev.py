from .base import *

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
