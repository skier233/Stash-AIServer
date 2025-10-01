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

# Copy migrations and application (for prod image)
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
RUN mkdir -p /app/data

#############################
# Dev stage (live reload)   #
#############################
FROM base AS dev
ENV AIO_DEVMODE=1
# In docker-compose dev we will bind mount ./backend/app over /app/app for live edits.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

#############################
# Prod stage (no reload)    #
#############################
FROM base AS prod
# Explicitly unset dev flag
ENV AIO_DEVMODE=
EXPOSE 8000
CMD ["python", "-m", "app.entrypoint"]
