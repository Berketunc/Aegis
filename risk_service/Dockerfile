FROM registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest
USER root
WORKDIR /app
COPY risk_service/requirements.txt /app/risk_service/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/risk_service/requirements.txt \
    && mkdir -p /data \
    && chown 1000:1000 /data
COPY risk_service /app/risk_service
ENV AEGIS_DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Internal Docker-network port only. Publish the gateway rather than this service.
EXPOSE 8000
USER 1000:1000
ENTRYPOINT ["python3", "-m", "uvicorn", "risk_service.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
