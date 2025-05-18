# Base image
FROM python:3.11-slim

# Set workdir
WORKDIR /app

# Install dependencies placeholder
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt || true

# Copy application code
COPY src/ src/

CMD ["bash"]
