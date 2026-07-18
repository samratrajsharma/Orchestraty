FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY sdk/requirements.txt ./sdk-requirements.txt
COPY mlops_agent/requirements.txt ./agent-requirements.txt
RUN pip install --no-cache-dir -r sdk-requirements.txt -r agent-requirements.txt

COPY sdk ./sdk
COPY mlops_agent ./mlops_agent

CMD ["python", "-m", "mlops_agent.agent"]
