FROM python:3.10-slim-bookworm

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg firefox-esr \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1

COPY . /app/

ENTRYPOINT ["python", "run_bot.py"]
CMD ["--data-dir", "/data"]
