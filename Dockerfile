FROM python:3.12-slim

RUN groupadd -r app && useradd -r -g app -d /app app

WORKDIR /app
COPY pyproject.toml ./
COPY apptrack ./apptrack
RUN pip install --no-cache-dir .

RUN mkdir -p /data && chown app:app /data
USER app

# Scheduler loop by default; `docker compose run --rm apptrack sync` for a one-off.
ENTRYPOINT ["python", "-m", "apptrack"]
CMD ["run"]
