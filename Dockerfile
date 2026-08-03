FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY alio_olio ./alio_olio
RUN pip install --no-cache-dir .
CMD ["alio-olio", "run"]
