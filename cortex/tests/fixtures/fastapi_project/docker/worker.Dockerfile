FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install .
COPY app/ app/
CMD ["python", "-m", "app.worker"]
