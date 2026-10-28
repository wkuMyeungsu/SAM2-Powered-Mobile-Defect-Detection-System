FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 SAM2_BUILD_CUDA=0

# GPU 없이 CPU 로 돈다. torch 를 먼저 깔아야 sam2 소스 패키지가 빌드된다.
RUN pip install --no-cache-dir setuptools wheel \
    torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://pypi.org/simple
COPY requirements.txt .
RUN pip install --no-cache-dir --no-build-isolation -r requirements.txt

# 가중치를 이미지에 미리 받아 둔다. 코드만 바꿔 다시 빌드하면 이 층은 캐시로 재사용된다.
ARG SAM2_MODEL=facebook/sam2.1-hiera-tiny
ENV SAM2_MODEL=${SAM2_MODEL}
RUN python -c "import os; from sam2.build_sam import _hf_download; _hf_download(os.environ['SAM2_MODEL'])"

COPY main.py judge.py segment.py sim_shapes.py test_inference.py ./
CMD ["python", "main.py"]
