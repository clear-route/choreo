# Chronicle — multi-stage build
# Stage 1: Build the React frontend
# Stage 2: Python runtime with built frontend as static files

# ── Frontend build ──
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ .
RUN npm run build

# ── Runtime ──
FROM python:3.12-slim
WORKDIR /app

# Install the chronicle package
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Copy built frontend into the static directory
COPY --from=frontend /app/frontend/dist ./src/chronicle/static

RUN pip install --no-cache-dir .

# Non-root user for production
RUN useradd --create-home chronicle
USER chronicle

EXPOSE 8000

# Single worker — required for in-memory SSE fan-out (ADR-0021).
# Multi-worker requires a LISTEN/NOTIFY bridge not included in v1.
CMD ["uvicorn", "chronicle.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
