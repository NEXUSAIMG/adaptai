FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Dev local: `import app.main` roda o create_all (schema a partir dos models,
# ENVIRONMENT != production) e o seed --simples e idempotente (login demo).
CMD python -c "import app.main" && python seed_demo.py --simples && \
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
