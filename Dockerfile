FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
# Use module form so relative imports and package structure work reliably
CMD ["python", "-u", "-m", "app.main"]
