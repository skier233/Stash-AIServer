FROM stash-ai-server:local
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir aiohttp
COPY reencode_worker /app/reencode_worker
EXPOSE 4154
CMD ["python", "-m", "reencode_worker.server"]
