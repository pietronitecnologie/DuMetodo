FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod
WORKDIR /app
RUN useradd --create-home --uid 1000 dumetodo
COPY pyproject.toml ./
COPY config ./config
COPY dumetodo ./dumetodo
RUN pip install .
COPY . .
RUN DJANGO_SECRET_KEY=build DATABASE_URL=sqlite:///tmp/x.db python manage.py collectstatic --noinput
USER dumetodo
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
