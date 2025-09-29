FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# база + директория под данные
RUN mkdir -p /app/data

ENV TZ=Europe/Amsterdam
ENV DB_PATH=/app/data/bot.db

CMD ["python", "-m", "app.bot"]
