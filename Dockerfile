FROM python:3.12-alpine

RUN apk add --no-cache ca-certificates tzdata

WORKDIR /app
COPY steam_bark.py /app/steam_bark.py

RUN mkdir -p /data
VOLUME ["/data"]

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    STATE_FILE=/data/state.json

CMD ["python", "/app/steam_bark.py"]
