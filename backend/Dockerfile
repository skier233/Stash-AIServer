# Dev Dockerfile for backend (fast reload)
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Create data directory for sqlite
RUN mkdir -p /app/data

# Copy only alembic + app code (mount will override in compose for dev)
COPY alembic.ini /app/
COPY alembic /app/alembic
COPY app /app/app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
