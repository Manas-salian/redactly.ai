# Base image - Ubuntu for better package availability and speed
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python and essential packages in one layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    python3-pip \
    tesseract-ocr \
    libpq-dev \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create symlinks for python
RUN ln -sf /usr/bin/python3.12 /usr/bin/python3 && \
    ln -sf /usr/bin/python3 /usr/bin/python

# Create a non-root user
RUN useradd -m -u 5000 appuser

COPY --chown=appuser:appuser server/requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Download spaCy model using pip
RUN pip install --no-cache-dir --break-system-packages \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl

COPY --chown=appuser:appuser server/ .

ENV FLASK_APP=app.py
ENV FLASK_ENV=production

USER appuser

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "120", "app:app"]
