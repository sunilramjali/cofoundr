FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY bridge ./bridge
COPY data ./data
EXPOSE 8080
# --no-access-log: request URLs contain the /brief token; keep it out of `fly logs`
CMD ["uvicorn", "bridge.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
