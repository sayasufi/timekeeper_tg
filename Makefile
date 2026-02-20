PYTHON ?= python
PIP ?= pip

install:
	$(PIP) install -e .[dev]

lint:
	ruff check .
	mypy app tests

test:
	pytest -q

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

worker:
	celery -A app.scheduler.celery_app.celery_app worker -l info

beat:
	celery -A app.scheduler.celery_app.celery_app beat -l info

migrate:
	alembic upgrade head

makemigration:
	alembic revision --autogenerate -m "$(m)"
