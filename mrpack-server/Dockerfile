FROM python:3.13-slim

WORKDIR /app

COPY server.py .

ENV HOST=0.0.0.0
ENV PORT=8080
ENV CACHE_DIR=/data/cache

EXPOSE 8080

CMD ["python", "-u", "server.py"]
