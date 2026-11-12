FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    LAUNCHER_HOST=0.0.0.0 \
    LAUNCHER_DATA=/data

WORKDIR /app
COPY app.py index.html ./

EXPOSE 8787
CMD ["python", "app.py"]
