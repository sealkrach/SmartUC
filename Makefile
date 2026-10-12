.PHONY: up down logs test dev-backend dev-frontend
up:            ; docker compose up --build -d
down:          ; docker compose down
logs:          ; docker compose logs -f backend
test:          ; cd backend && .venv/bin/python -m pytest -q
dev-backend:   ; cd backend && .venv/bin/uvicorn app.main:app --reload
dev-frontend:  ; cd frontend && npm run dev
