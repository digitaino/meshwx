FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "meshcore>=2.3" "paho-mqtt>=2.0"
COPY observer.py /app/observer.py
CMD ["python", "/app/observer.py"]
