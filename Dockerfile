# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE="1"
ENV PYTHONUNBUFFERED="1"

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y ffmpeg

# Install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . /app/

# Copy entrypoint script
COPY entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

# Collect static files
# Provide a dummy key during build to allow collectstatic to run
RUN GEMINI_MODEL="dummy-key" GEMINI_API_KEY="dummy-key" DJANGO_SECRET_KEY="dummy-key" REHOST_BASE_URL="dummy-key" python manage.py collectstatic --noinput

# Expose the port Gunicorn will run on
EXPOSE 8000

# Set the entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]

# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "podslice.wsgi:application"]
