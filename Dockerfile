FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY fleet ./fleet
RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 fleet \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin fleet \
    && install -d -m 0700 -o 10001 -g 10001 /data

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import http.client; c = http.client.HTTPConnection('127.0.0.1', 8787, timeout=3); c.request('GET', '/'); r = c.getresponse(); status = r.status; r.read(); c.close(); raise SystemExit(0 if status == 200 else 1)"]
ENTRYPOINT ["python", "-m", "fleet.container"]
