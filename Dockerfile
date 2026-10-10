
FROM python:3.12-slim
WORKDIR /app
COPY . .
CMD ["python", "-u", "radar_crypto_binance.py"]
