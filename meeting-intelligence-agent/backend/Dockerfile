FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (gcc/g++ for compiling, ffmpeg for audio,
# remaining libs required by Playwright/Chromium on Debian slim)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    ffmpeg \
    libsndfile1 \
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
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && (apt-get install -y libasound2 || apt-get install -y libasound2t64 || true) \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Download Playwright's Chromium browser binary (Python package already installed above)
RUN playwright install chromium

# Copy application
COPY . .

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
