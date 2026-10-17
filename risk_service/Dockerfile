FROM registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest
USER root
WORKDIR /app
# cryptography is built from source on this s390x image. Its OpenSSL headers
# must be installed inside the build image, independently of the VM's OS.
RUN if command -v apt-get >/dev/null 2>&1; then \
      apt-get update \
      && apt-get install -y --no-install-recommends build-essential libssl-dev libffi-dev python3-dev pkg-config \
      && rm -rf /var/lib/apt/lists/*; \
    elif command -v dnf >/dev/null 2>&1; then \
      dnf install -y gcc openssl-devel libffi-devel python3-devel redhat-rpm-config pkgconf-pkg-config \
      && dnf clean all; \
    elif command -v microdnf >/dev/null 2>&1; then \
      microdnf install -y gcc openssl-devel libffi-devel python3-devel redhat-rpm-config pkgconf-pkg-config \
      && microdnf clean all; \
    elif command -v yum >/dev/null 2>&1; then \
      yum install -y gcc openssl-devel libffi-devel python3-devel redhat-rpm-config pkgconfig \
      && yum clean all; \
    else \
      echo "No supported package manager for cryptography build dependencies" >&2; exit 1; \
    fi \
    && pkg-config --modversion openssl \
    && pkg-config --atleast-version=3.0.0 openssl
COPY risk_service/requirements.txt /app/risk_service/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/risk_service/requirements.txt \
    && mkdir -p /data \
    && chown 1000:1000 /data
COPY risk_service /app/risk_service
COPY privacy /app/privacy
ENV AEGIS_DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Internal Docker-network port only. Publish the gateway rather than this service.
EXPOSE 8000
USER 1000:1000
ENTRYPOINT ["python3", "-m", "uvicorn", "risk_service.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
