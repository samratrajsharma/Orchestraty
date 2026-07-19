FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY sdk/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY sdk ./sdk
COPY recovery_agent ./recovery_agent

CMD ["python", "-m", "recovery_agent.agent"]
