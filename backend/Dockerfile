FROM --platform=linux/arm64 python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent_server.py .
COPY template.html.j2 .

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "agent_server:app", "--host", "0.0.0.0", "--port", "8080"]
