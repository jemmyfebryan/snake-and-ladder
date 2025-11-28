FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy only requirements to leverage Docker cache
COPY requirements.txt .

# Install dependencies (without system packages)
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Now copy the rest of the application
COPY . .

# Expose the port (optional for documentation)
EXPOSE 8080

# Start the app
CMD ["uvicorn", "snake_ladder_api:app", "--host", "0.0.0.0", "--port", "8080"]