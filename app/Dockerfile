FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY assistant ./assistant
COPY eval ./eval
COPY tests ./tests
COPY pytest.ini .
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "assistant.main:app", "--host", "0.0.0.0", "--port", "8000"]
