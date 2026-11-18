# The proxy image runs every part of the stack: core, facade, or both in one
# process. Which part a container runs is chosen with `--mode` at run time, so a
# single image serves the whole Compose file.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY proxy ./proxy

# Run unprivileged; all the ports the proxy uses are above 1024.
# (Not named "proxy": Debian's base image already defines that user.)
RUN useradd --system --create-home --uid 10001 proxyapp
USER proxyapp

EXPOSE 8899/udp
EXPOSE 8787/tcp

ENTRYPOINT ["python", "-m", "proxy"]
CMD ["--config", "/config/inverter_proxy.yaml"]
