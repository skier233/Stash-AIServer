#############################
# Base stage (dependencies) #
#############################
FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Prepare wheel build area (builder will create a wheel)
RUN mkdir -p /wheels

RUN mkdir -p /app/data

#############################
# Dev stage (live reload)   #
#############################
FROM base AS dev
ENV AIO_DEVMODE=1
# In docker-compose dev we will bind mount ./backend/app over /app/app for live edits.
CMD ["python", "-m", "app.dev_entrypoint"]

#############################
# Prod stage (no reload)    #
#############################
FROM base AS prod
# Explicitly unset dev flag
ENV AIO_DEVMODE=
EXPOSE 8000
# Build wheel in a temporary stage using the full source (copied here)
FROM base AS builder
WORKDIR /src
# Copy project files for wheel build
COPY pyproject.toml setup.cfg MANIFEST.in /src/
COPY app /src/app
COPY alembic.ini /src/alembic.ini
COPY alembic /src/alembic
COPY requirements.txt /src/requirements.txt
RUN python -m pip wheel . -w /wheels

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir --upgrade pip
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl
# Ensure alembic files exist in the runtime image (packaged OR copied)
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
RUN mkdir -p /app/data

CMD ["python", "-m", "app.entrypoint"]
