ARG ML_IMAGE=registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest
FROM ${ML_IMAGE}

USER root
WORKDIR /opt/aegis
COPY llm/requirements.txt /opt/aegis/llm/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /opt/aegis/llm/requirements.txt
COPY llm /opt/aegis/llm
# Import the application too, so a route-definition error fails the build, not the container start.
RUN python3 -c 'from llama_cpp import Llama; import fastapi, pydantic, uvicorn; import llm.app'
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 1000
# Internal Docker network only. Keep one process to avoid a model copy per worker.
ENTRYPOINT ["python3", "-m", "uvicorn"]
CMD ["llm.app:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--no-access-log"]
