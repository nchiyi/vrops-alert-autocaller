# ========================================
# Stage 1: Builder（編譯 pjsua2）
# ========================================
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    libasound2-dev \
    libopus-dev \
    swig \
    python3-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# 編譯 pjsua2（支援 SIP TLS）
RUN cd /tmp && \
    git clone --depth 1 https://github.com/pjsip/pjproject.git && \
    cd pjproject && \
    ./configure --enable-shared --with-ssl=/usr CFLAGS="-fPIC" && \
    make -j$(nproc) && \
    make install && \
    cd pjsip-apps/src/swig/python && \
    make && make install && \
    ldconfig

# 安裝 Python 依賴到獨立前綴（供後續 stage copy）
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ========================================
# Stage 2: Runtime（僅含執行環境）
# ========================================
FROM python:3.11-slim

# 只安裝執行時需要的動態庫
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libssl3 \
    libasound2 \
    libopus0 \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

# 複製 pjsua2 編譯產物
COPY --from=builder /usr/local/lib/libpj*.so* /usr/local/lib/
COPY --from=builder /usr/local/lib/python3.11/site-packages/pjsua2* \
     /usr/local/lib/python3.11/site-packages/
COPY --from=builder /usr/local/lib/python3.11/site-packages/_pjsua2* \
     /usr/local/lib/python3.11/site-packages/
RUN ldconfig

# 複製 pip 安裝的 Python 依賴
COPY --from=builder /install /usr/local

WORKDIR /app

# 複製應用程式碼（由 Agent B/C/D 提供）
COPY *.py ./
COPY web/ ./web/
COPY config/ ./config/

RUN mkdir -p audio logs

# 預先合成備援語音（失敗不中斷 build）
RUN python tts_engine.py --generate-fallback || true

EXPOSE 5000

# workers=1 threads=4（避免狀態不共享 + SIP 衝突）
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "120", \
     "--access-logfile", "/app/logs/access.log", \
     "--error-logfile", "/app/logs/gunicorn-error.log", \
     "webhook_server:app"]
