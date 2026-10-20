FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY twin_core /opt/twin_core
COPY simulator/requirements.txt .
RUN pip install --no-cache-dir /opt/twin_core -r requirements.txt

COPY simulator/main.py .
CMD ["python", "main.py"]
