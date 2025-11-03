#############################
# Simple runtime image that installs the package from PyPI
#############################
#############################
# Base stage with dependencies
#############################
FROM python:3.14-slim AS base
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
# In docker-compose dev we bind mount ./backend/stash_ai_server -> /app/stash_ai_server for live edits.
CMD ["python", "-m", "stash_ai_server.dev_entrypoint"]

#############################
# Runtime stage (uses PyPI package)
#############################
FROM base AS runtime
ARG VERSION=latest
RUN set -eux; \
	INSTALL_CMD=""; \
	if [ "$VERSION" = "latest" ]; then \
		INSTALL_CMD="pip install --no-cache-dir stash-ai-server"; \
	else \
		INSTALL_CMD="pip install --no-cache-dir stash-ai-server==${VERSION}"; \
	fi; \
	echo "Will run: $INSTALL_CMD"; \
	n=0; \
	until [ $n -ge 5 ]; do \
		if sh -c "$INSTALL_CMD"; then \
			echo "install succeeded"; break; \
		fi; \
		n=$((n+1)); \
		echo "install attempt $n failed; retrying in 20s"; \
		sleep 20; \
	done; \
	if [ $n -ge 5 ]; then \
		echo "install failed after 5 attempts"; exit 1; \
	fi

RUN mkdir -p /app/data

EXPOSE 4153
CMD ["python", "-m", "stash_ai_server.entrypoint"]
