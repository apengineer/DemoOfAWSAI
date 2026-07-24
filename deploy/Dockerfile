# Amazon Bedrock AgentCore Runtime requires linux/arm64 (Graviton).
# Build from the PROJECT ROOT so backend/ and config/ are in the context:
#   docker buildx build --platform linux/arm64 -f deploy/Dockerfile -t ekyc-agent:arm64 .
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY deploy/requirements.txt /app/deploy/requirements.txt
RUN pip install --no-cache-dir -r /app/deploy/requirements.txt

# Copy application code + configuration.
COPY backend/ /app/backend/
COPY config/ /app/config/
COPY runtime_entrypoint.py /app/runtime_entrypoint.py

# AgentCore Runtime contract: serve /invocations (POST) and /ping (GET) on 8080.
EXPOSE 8080

CMD ["python", "runtime_entrypoint.py"]
