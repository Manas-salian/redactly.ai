# Nginx

TLS terminator for the V2 stack. Reverse-proxies `/api/*` to the FastAPI service.

## Local dev cert

Run:

    openssl req -x509 -nodes -newkey rsa:2048 \
        -keyout nginx/dev.key -out nginx/dev.crt \
        -days 365 -subj "/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

The cert/key are gitignored. Each developer regenerates them on first run.

## Production

Replace `nginx/dev.crt` and `nginx/dev.key` with real certificates (Let's Encrypt
or your CA-issued pair) and bake them in via Docker secrets or a volume mount.
