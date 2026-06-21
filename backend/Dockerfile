FROM python:3.11-slim

WORKDIR /app

# Системные зависимости для rasterio, psycopg2, shapely
RUN apt-get update && apt-get install -y \
    gcc g++ \
    libgdal-dev \
    libpq-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
