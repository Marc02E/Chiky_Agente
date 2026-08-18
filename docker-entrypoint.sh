#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

exec uvicorn personal_ai_secretary.api.app:app --host 0.0.0.0 --port 8000