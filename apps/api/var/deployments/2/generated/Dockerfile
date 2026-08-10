FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app
COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=10001:10001 . .
USER 10001
EXPOSE 5000
CMD ["sh", "-c", "flask --app wsgi run --host 0.0.0.0 --port 5000"]
