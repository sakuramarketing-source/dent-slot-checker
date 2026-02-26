# Playwright公式イメージ（Chromium + 全依存ライブラリ同梱）
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

# Set working directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers are pre-installed in the base image
# 念のためChromiumだけ再インストール（バージョン整合性）
RUN playwright install chromium

# Copy application code
COPY . .

# Create output and logs directories
RUN mkdir -p output logs

# Set environment variables
ENV FLASK_APP=web.app
ENV FLASK_ENV=production
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8080

# Run with gunicorn
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 "web.app:create_app()"
