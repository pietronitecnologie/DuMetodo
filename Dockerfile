FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod
WORKDIR /app
RUN useradd --create-home --uid 1000 metodo
COPY pyproject.toml ./
COPY config ./config
COPY metodo ./metodo
RUN pip install .
COPY . .
RUN DJANGO_SECRET_KEY=build DATABASE_URL=sqlite:///tmp/x.db python manage.py collectstatic --noinput
RUN mkdir -p /app/media && chown metodo /app/media
# In fondo: cambia a ogni commit e non deve invalidare la cache delle dipendenze.
ARG METODO_VERSIONE=sconosciuta
ENV METODO_VERSIONE=$METODO_VERSIONE
USER metodo
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
