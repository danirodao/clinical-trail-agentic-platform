FROM python:3.12-slim

WORKDIR /app

COPY migrations/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY migrations/ .

# Wait for PG, then run migrations
CMD ["sh", "-c", "sleep 5 && alembic upgrade head"]