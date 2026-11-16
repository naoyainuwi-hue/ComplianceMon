FROM python:3.12-slim

# iputils-ping: server.py shells out to the OS "ping" command to check host
# status (browsers can't send raw ICMP themselves) — this is NOT installed
# in the slim base image by default, so it has to be added explicitly or
# every "Ping" button in the app will silently fail inside the container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Only the two files the server actually needs at runtime.
COPY server.py .
COPY ComplianceAutoFilter.html .

# compliance_data.db is NOT copied in — it's created on first run (as a
# SQLite database, WAL mode) and is meant to live on a mounted volume (see
# docker-compose.yml) so the data survives container rebuilds/updates, not
# just restarts. If a compliance_data.json from the old file-based version
# is present in that same mounted folder, server.py migrates it into the new
# SQLite database automatically the first time it starts.

EXPOSE 8000

# --db points inside the container at the path the compose file mounts as a
# volume. --host 0.0.0.0 is required (default already, kept explicit here)
# so the container accepts connections from other PCs, not just itself.
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000", "--db", "/data/compliance_data.db"]
