# FantiaJp WebUI - scraper UI
#
# Build:  docker build -t fantia-webui .
# Run:    see docker-compose.yml or:
#         docker run -d --name fantia-webui -p 8799:8799 \
#           -e CC_COOKIECLOUD_URL=http://192.168.2.210:8188 \
#           -e CC_COOKIECLOUD_KEY=<your-key-uuid> \
#           -e CC_COOKIECLOUD_PASSWORD=<your-password> \
#           -v /path/to/data:/data \
#           fantia-webui
FROM python:3.12-alpine

WORKDIR /app

# webui.py is stdlib-only; fantiajp.py lazily imports `requests` for
# CookieCloud / fantia.jp traffic. Override the index for faster builds:
#   docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t fantia-webui .
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY fantiajp.py webui.py ./
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} requests

# Persist webui_config.json and the CookieCloud cookie cache under /data.
ENV WEBUI_HOST=0.0.0.0 \
    WEBUI_PORT=8799 \
    WEBUI_CONFIG_DIR=/data \
    CC_CACHE_DIR=/data

VOLUME ["/data"]
EXPOSE 8799

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
    CMD wget -qO- http://127.0.0.1:8799/api/status >/dev/null 2>&1 || exit 1

CMD ["python", "webui.py"]
