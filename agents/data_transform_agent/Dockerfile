FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY sdk/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY sdk ./sdk
COPY data_transform_agent ./data_transform_agent

CMD ["python", "-m", "data_transform_agent.agent"]
