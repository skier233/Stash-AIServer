#############################
# Simple runtime image that installs the package from PyPI
#############################
#############################
# Base stage with dependencies
#############################
FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt || true

#############################
# Dev stage for live reload
#############################
FROM base AS dev
ENV AIO_DEVMODE=1
# In docker-compose dev we will bind mount ./backend/app over /app/app for live edits.
CMD ["python", "-m", "app.dev_entrypoint"]

#############################
# Runtime stage (uses PyPI package)
#############################
FROM base AS runtime
ARG VERSION=latest
RUN if [ "$VERSION" = "latest" ]; then \
			pip install --no-cache-dir stash-ai-server; \
		else \
			pip install --no-cache-dir stash-ai-server==${VERSION}; \
		fi

# Ensure alembic files are present for runtime migrations (optional)
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["python", "-m", "app.entrypoint"]
