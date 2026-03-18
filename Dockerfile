FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir aiosqlite aiohttp

# Copy source
COPY . .

# Install the package
RUN pip install --no-cache-dir .

CMD ["python", "-m", "dispatcher"]
