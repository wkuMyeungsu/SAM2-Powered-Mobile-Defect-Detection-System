FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py preprocess.py forward.py test_preprocess.py test_service.py ./
CMD ["python", "main.py"]
