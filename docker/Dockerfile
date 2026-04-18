FROM python:3.12-slim

RUN useradd -m -u 1000 pyra

WORKDIR /app

COPY pyproject.toml README.md ./
COPY pybot/ ./pybot/
RUN pip install --no-cache-dir ".[postgres]"

COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY config/config.example.yaml /app/config.example.yaml

VOLUME ["/data", "/plugins_extra"]

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER pyra

ENTRYPOINT ["/entrypoint.sh"]
CMD ["--config", "/data/config.yaml"]
