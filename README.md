# Podslice

Podslice is a Django-based application designed to download podcast episodes, intelligently slice them into smaller, ad-free audio segments, and rehost them for a seamless listening experience. It empowers users to enjoy their favorite podcasts without interruptions, while providing a robust platform for managing and serving re-hosted content.

## Capabilities & Features

*   **Podcast Subscription & Management:** Easily subscribe to any podcast RSS feed via a simple web interface.
*   **Automated Episode Processing:** Automatically fetches new episodes, downloads audio, and processes them in the background using Python threads.
*   **Intelligent Ad Detection & Slicing:** Utilizes Google's Generative AI to identify and remove ad segments from audio, creating a cleaner listening experience.
*   **Re-hosted RSS Feeds:** Generates new RSS feeds for your sliced podcasts, allowing you to subscribe to the ad-free versions in your preferred podcast client.
*   **OPML Import/Export:** Export your podcast subscriptions in OPML format for easy migration or backup, and import subscriptions from other podcast clients.
*   **Episode Status Tracking:** Monitor the processing status of each episode (downloading, analyzing, processing, complete, failed) via the web interface.
*   **On-demand Reprocessing:** Manually trigger reprocessing of podcasts.

## Technologies Used

*   **Backend:** Django, Django REST Framework
*   **Asynchronous Tasks:** Python's `threading` module
*   **AI Integration:** Google Generative AI (for ad detection)
*   **Audio Manipulation:** `ffmpeg`
*   **RSS Parsing:** `feedparser`
*   **HTTP Requests:** `requests`
*   **Database:** SQLite

## Getting Started

To get started with Podslice, you'll need Python 3.8+ and pip.

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/prashmohan/podslice.git
    cd podslice
    ```

2.  **Set up a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Set Environment Variables:**
    Before running the application, you must set the following environment variables. You can create a `.env` file in the project root to store these variables.

    *   `DJANGO_SECRET_KEY`: A unique secret key for your Django project. You can generate one using `python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'`
    *   `GEMINI_API_KEY`: Your API key for Google Generative AI.
    *   `REHOST_BASE_URL`: The base URL where your re-hosted media will be served (e.g., `http://localhost:8000`).

    Example `.env` file:
    ```
    DJANGO_SECRET_KEY='your_generated_secret_key'
    GEMINI_API_KEY='your_gemini_api_key'
    REHOST_BASE_URL='http://localhost:8000'
    MAX_EPISODES_PER_PODCAST=10
    ```

## Running the Application

Podslice is a monolithic Django application.

```bash
python manage.py runserver
```

This single command starts the Django development server, which handles the web interface, API endpoints, and serves the re-hosted media. Background tasks for processing podcasts are managed using Python threads within the same process.

## Database Migrations

After making changes to models, you'll need to create and apply migrations.

```bash
python manage.py makemigrations
python manage.py migrate
```

## Running Tests

To run the tests for the `podcasts` app:

```bash
python manage.py test podcasts
```

## Code Style and Conventions

*   Follow Django's coding style.
*   Keep models, views, and serializers in their respective files.
*   Use Python threads for any long-running tasks to avoid blocking web requests.
*   Write tests for new features and bug fixes.