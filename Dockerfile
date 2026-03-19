FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml .
RUN python -c \
    "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" \
    > /tmp/requirements.txt && pip install --no-cache-dir -r /tmp/requirements.txt

# Copy source
COPY . .

# Install the package
RUN pip install --no-cache-dir .

CMD ["python", "-m", "dispatcher"]
