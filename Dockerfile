# Ubuntu 24.04 traz COLMAP 3.9 (testado). A imagem python:slim (Debian) traz uma versão mais antiga.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    QT_QPA_PLATFORM=offscreen \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        colmap python3 python3-venv python3-pip \
        libgl1 libegl1 libgomp1 libglib2.0-0 libsm6 libxext6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data

EXPOSE 8080
CMD ["python3", "app.py"]
