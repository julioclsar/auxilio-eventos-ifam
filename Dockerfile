FROM python:3.12-slim

# LibreOffice converte os anexos .docx em PDF. As fontes evitam que o texto mude de largura.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 DJANGO_DATA_DIR=/dados
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput

VOLUME /dados
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py configurar && exec gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 2 --timeout 180"]
