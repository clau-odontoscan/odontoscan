FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Dependências do sistema
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-dev \
    colmap \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 libxext6 libxrender-dev \
    wget curl git \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copia o projeto
COPY . .

# Cria pastas necessárias
RUN mkdir -p /app/uploads /app/scans /data

# Porta
EXPOSE 5050

# Inicia com gunicorn
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--bind", "0.0.0.0:5050", "--timeout", "300", "app:app"]
