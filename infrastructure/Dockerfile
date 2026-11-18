FROM python:3.12-slim
WORKDIR /lab
COPY agent /lab/agent
COPY endpoints /lab/endpoints
USER 10001:10001
ENTRYPOINT ["python", "-m", "endpoints.server"]
