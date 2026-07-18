FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY sdk/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY sdk ./sdk
COPY deploy_agent ./deploy_agent

CMD ["python", "-m", "deploy_agent.agent"]
