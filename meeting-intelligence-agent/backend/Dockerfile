FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    ffmpeg \
    libsndfile1 \
    # Virtual display (Xvfb) — lets Chromium run headed inside Docker,
    # bypasses Google's headless browser detection
    xvfb \
    x11-utils \
    pulseaudio \
    # Chromium runtime dependencies not pulled in by --with-deps on slim
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    && (apt-get install -y libasound2 || apt-get install -y libasound2t64 || true) \
    && rm -rf /var/lib/apt/lists/*

# Entrypoint: start Xvfb + PulseAudio, then exec the app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Copy requirements
COPY requirements.txt .

# Install CPU-only PyTorch first — avoids the 419 MB CUDA wheel that pip
# picks up by default (nvidia_cudnn_cu13 alone is another 433 MB).
# Shell retry loop handles hash mismatches from corrupted/truncated downloads
# (pip --retries only retries at the HTTP level, not after hash failure).
RUN for i in 1 2 3; do \
      pip install --no-cache-dir --timeout=600 \
          torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
      && break; \
      [ $i -lt 3 ] && echo "Attempt $i/3 failed — retrying in 60s..." && sleep 60; \
    done; \
    python -c "import torch" && echo "torch installed OK"

# Install remaining Python dependencies.
# torch/torchaudio are already satisfied above so pip won't re-download them.
RUN pip install --no-cache-dir --timeout=300 --retries=5 -r requirements.txt

# Download Playwright's Chromium browser binary (Python package already installed above)
RUN playwright install chromium

# Copy application
COPY . .

# Expose port
EXPOSE 8000

# Run application via entrypoint (starts Xvfb + PulseAudio first)
CMD ["/entrypoint.sh", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
