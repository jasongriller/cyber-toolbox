FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY app/ ./app/

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Temp directory for job files. Owned by the runtime uid, since that is the
# only path this process writes to; everything else stays root-owned and
# read-only to it (COPY's default).
RUN mkdir -p /tmp/stig-parser-jobs \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin stigparser \
    && chown 10001:10001 /tmp/stig-parser-jobs
ENV STIG_TEMP_DIR=/tmp/stig-parser-jobs

EXPOSE 5000

# Non-root: this process parses attacker-supplied scan files, so it must not
# also own the filesystem it runs on. Matches converter.Dockerfile (uid 10001).
USER 10001

CMD ["python", "-m", "flask", "--app", "app.web:create_app", "run", "--host", "0.0.0.0", "--port", "5000"]
