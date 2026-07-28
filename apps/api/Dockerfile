FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        openssh-client \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .

EXPOSE 5000

CMD [
  "gunicorn",
  "--workers", "1",
  "--threads", "4",
  "--bind", "0.0.0.0:5000",
  "--timeout", "180",
  "--access-logfile", "-",
  "--error-logfile", "-",
  "wsgi:app"
]