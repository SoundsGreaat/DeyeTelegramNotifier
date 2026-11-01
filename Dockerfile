FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY locales.py .
COPY energy_stats.py .
COPY db_migrations.py alembic.ini ./
COPY migrations ./migrations

CMD ["python", "main.py"]
