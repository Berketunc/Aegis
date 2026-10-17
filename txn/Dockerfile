# Claims transaction CLMI: gateway + COBOL decision rule.
# Build from the repo root:  docker build -f txn/Dockerfile -t clmi-gateway .
# python:3.11-slim is multi-arch (includes s390x for LinuxONE).
FROM python:3.11-slim

RUN apt-get update \
 && (apt-get install -y --no-install-recommends gnucobol3 || apt-get install -y --no-install-recommends gnucobol) \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY txn/requirements.txt txn/requirements.txt
RUN pip install --no-cache-dir -r txn/requirements.txt

COPY privacy privacy
COPY llm/prompts llm/prompts
COPY txn txn

# The decision rule is compiled inside the image, on the target architecture.
RUN cobc -x -o txn/CLMIPOL txn/CLMIPOL.cbl \
 && echo "00100 08500 06000 0 0 1 0" | txn/CLMIPOL | grep -q "^GREEN NONE$"

RUN useradd --system --no-create-home clmi && mkdir /data && chown clmi /data
USER clmi

ENV DB_PATH=/data/clmi.db \
    CLMIPOL_BIN=/app/txn/CLMIPOL \
    POLICY_ENGINE=cobol \
    UI_DIR=/app/ui \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "txn.gateway.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
