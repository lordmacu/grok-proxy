FROM python:3.12-slim

WORKDIR /app

# grpcio en slim necesita compilar extensiones C
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY docker-api/requirements.txt .

# Instalar wheel primero para evitar builds desde fuente donde haya wheels
RUN pip install --no-cache-dir wheel && \
    pip install --no-cache-dir -r requirements.txt

# Proto stubs compilados (se copian desde la raíz del repo)
COPY auth_mgmt_pb2.py \
     auth_mgmt_pb2_grpc.py \
     auth_frontend_pb2.py \
     auth_frontend_pb2_grpc.py \
     grok_api_pb2.py \
     grok_api_pb2_grpc.py \
     ./

# Código de la API
COPY docker-api/main.py \
     docker-api/grok_backend.py \
     docker-api/auth_backend.py \
     ./

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
