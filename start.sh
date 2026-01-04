#!/bin/bash
set -e

# Create network if not exists
docker network create app-network || true

# Build Server
echo "Building server..."
docker build -t redactlyai_server:latest -f server/Dockerfile .

# Build Client
echo "Building client..."
docker build -t redactlyai_client:latest -f client/Dockerfile .

# Stop existing containers
echo "Stopping old containers..."
docker rm -f redactlyai_server_1 redactlyai_client_1 || true

# Run Server
echo "Starting server..."
docker run -d \
  --name redactlyai_server_1 \
  --network app-network \
  -p 5000:5000 \
  redactlyai_server:latest

# Run Client
echo "Starting client..."
docker run -d \
  --name redactlyai_client_1 \
  --network app-network \
  -p 3000:3000 \
  -e VITE_API_URL=http://localhost:5000 \
  redactlyai_client:latest

echo "Application started!"
echo "Client: http://localhost:3000"
echo "Server: http://localhost:5000/health"
