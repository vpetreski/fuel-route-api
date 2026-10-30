FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN DJANGO_DEBUG=0 DJANGO_SECRET_KEY=collectstatic python manage.py collectstatic --noinput \
    && useradd --create-home app \
    && mkdir -p /app/var \
    && chown app /app/var

USER app
ENV DJANGO_DEBUG=0 \
    SQLITE_PATH=/app/var/db.sqlite3 \
    CACHE_DIR=/app/var/cache

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/', timeout=4)"

# The schema and the station data are loaded on start (about two seconds, idempotent).
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py import_fuel_prices && exec gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 3 --preload --access-logfile -"]
