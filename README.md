# Podslice

Podslice is a Django-based application designed to download podcast episodes, intelligently slice them into smaller, ad-free audio segments, and rehost them for a seamless listening experience. It empowers users to enjoy their favorite podcasts without interruptions, while providing a robust platform for managing and serving re-hosted content.

## Capabilities & Features

*   **Podcast Subscription & Management:** Easily subscribe to any podcast RSS feed.
*   **Automated Episode Processing:** Automatically fetches new episodes, downloads audio, and processes them.
*   **Intelligent Ad Detection & Slicing:** Utilizes advanced AI (Google Generative AI) to identify and remove ad segments from audio, creating a cleaner listening experience.
*   **Re-hosted RSS Feeds:** Generates new RSS feeds for your sliced podcasts, allowing you to subscribe to the ad-free versions in your preferred podcast client.
*   **OPML Export:** Export your podcast subscriptions in OPML format for easy migration or backup.
*   **Episode Status Tracking:** Monitor the processing status of each episode (downloading, analyzing, processing, complete, failed).
*   **On-demand Reprocessing:** Manually trigger reprocessing of individual podcasts or episodes.
*   **Scalable Architecture:** Built with Django and Celery for efficient asynchronous task processing, capable of handling numerous podcasts and episodes.

## Technologies Used

*   **Backend:** Django, Django REST Framework
*   **Asynchronous Tasks:** Celery
*   **AI Integration:** Google Generative AI (for ad detection)
*   **Audio Manipulation:** `pydub`
*   **RSS Parsing:** `feedparser`
*   **HTTP Requests:** `requests`
*   **Database:** SQLite (default, configurable for production)

## Getting Started

To get started with Podslice, you'll need Python 3.8+ and pip.

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-repo/podslice.git
    cd podslice
    ```

2.  **Set up a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r podslice_core/requirements.txt
    ```

4.  **Set Environment Variables:**
    Before running the application, you must set the following environment variables:
    *   `DJANGO_SECRET_KEY`: A unique secret key for your Django project. You can generate one using `python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'`
    *   `GEMINI_API_KEY`: Your API key for Google Generative AI.
    *   `REHOST_BASE_URL`: The base URL where your re-hosted media will be served (e.g., `http://localhost:8001`).

    Example (for Linux/macOS):
    ```bash
    export DJANGO_SECRET_KEY='your_generated_secret_key'
    export GEMINI_API_KEY='your_gemini_api_key'
    export REHOST_BASE_URL='http://localhost:8001'
    ```

## Running the Application

Podslice consists of two main components: the core Django application and the rehosting server.

### 1. Core Application (Django Server)

This handles the main logic, API endpoints, and UI for subscription and status.

```bash
python podslice_core/manage.py runserver
```

### 2. Celery Worker (for Asynchronous Tasks)

This processes long-running tasks like podcast polling, audio downloading, ad detection, and slicing.

```bash
celery -A podslice_core.podslice_core worker -l info
```

### 3. Rehosting Application (Django Server)

This is a lightweight server specifically for serving the sliced audio files. It's recommended to run this on a different port.

```bash
python podslice_rehost/manage.py runserver 8001
```
*(Note: The `podslice_rehost` project is not yet implemented in this repository, but is part of the overall system design.)*

## Database Migrations

After making changes to models, you'll need to create and apply migrations.

```bash
python podslice_core/manage.py makemigrations
python podslice_core/manage.py migrate
```

## Running Tests

To run the tests for the `podcasts` app:

```bash
python podslice_core/manage.py test podcasts
```

## Code Style and Conventions

*   Follow Django's coding style.
*   Keep models, views, and serializers in their respective files.
*   Use Celery for any long-running tasks to avoid blocking web requests.
*   Write tests for new features and bug fixes.
