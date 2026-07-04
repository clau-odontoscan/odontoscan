FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Instala COLMAP e dependências
RUN apt-get update && apt-get install -y \
    colmap \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data /app/uploads /app/scans

EXPOSE 8080

CMD ["python3", "app.py"]
