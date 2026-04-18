FROM python:3.12-slim

RUN useradd -m -u 1000 pyra

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[postgres]"

COPY pybot/ ./pybot/
COPY alembic/ ./alembic/
COPY alembic.ini ./

VOLUME ["/data", "/plugins_extra"]

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER pyra

ENTRYPOINT ["/entrypoint.sh"]
CMD ["--config", "/data/config.yaml"]
