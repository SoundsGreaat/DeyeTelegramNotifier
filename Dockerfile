FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY deye_notifier ./deye_notifier
COPY alembic.ini ./
COPY migrations ./migrations

CMD ["python", "-m", "deye_notifier"]
