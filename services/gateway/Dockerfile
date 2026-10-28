FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 휴대폰 카메라는 HTTPS 주소에서만 열리므로, cloudflared 임시 터널로 공개 HTTPS 주소를 만든다.
# 받기에 실패해도 빌드는 계속되고, gateway는 터널 없이 뜬다 (/health의 tunnel에 이유 표시).
ARG CLOUDFLARED_VERSION=2026.10.0
RUN ARCH="$(dpkg --print-architecture)" \
    && python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/usr/local/bin/cloudflared')" \
       "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${ARCH}" \
    && chmod +x /usr/local/bin/cloudflared \
    && cloudflared --version \
    || echo "cloudflared 받기 실패: 터널 없이 실행됩니다"

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

COPY pytest.ini ./
COPY app ./app
COPY tests ./tests

# 포트는 docker-compose.yml의 PORT(8001)를 따른다
CMD ["python", "-m", "app"]
