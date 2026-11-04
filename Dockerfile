FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --system --no-create-home kura
USER kura
ENV PORT=8002
EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request as u;u.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8002')+'/health',timeout=4)"
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8002}"]
