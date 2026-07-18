FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY sdk/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY sdk ./sdk
COPY infra_agent ./infra_agent

CMD ["python", "-m", "infra_agent.agent"]
