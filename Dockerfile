FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy server code (no external dependencies needed)
COPY server.py .
COPY event_store.py .
COPY requirements.txt .
COPY .env .

RUN pip install -r requirements.txt

ENV HOST=0.0.0.0
ENV PORT=8080

EXPOSE 8080

# Run the simple server
CMD ["python3", "server.py"]
