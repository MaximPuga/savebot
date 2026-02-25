FROM python:3.11-slim

WORKDIR /app

# Устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY Main.py .
COPY config.py .

# Создаем папку для скачиваний
RUN mkdir -p /app/downloads

# Запускаем бота
CMD ["python", "Main.py"]
