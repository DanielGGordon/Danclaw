FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir $(python -c \
    "import tomllib; print(' '.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))")

# Copy source
COPY . .

# Install the package
RUN pip install --no-cache-dir .

CMD ["python", "-m", "dispatcher"]
