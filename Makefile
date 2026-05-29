PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn

.PHONY: up down logs app-install ml-install run-app run-ml build-ml

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

build-ml:
	docker compose build ml_service

app-install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -r requirements.txt

ml-install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -r ml_service/requirements.txt

run-app:
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload

run-ml:
	cd ml_service && ../$(UVICORN) app:app --host 0.0.0.0 --port 8001 --reload
