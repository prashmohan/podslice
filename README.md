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
*   **Per-podcast Retention Settings:** Configure how many episodes to keep for each subscription, with an option for unlimited retention.

## Technologies Used

*   **Backend:** Django, Django REST Framework
*   **Asynchronous Tasks:** Python's `threading` module
*   **AI Integration:** Google Generative AI (for ad detection)
*   **Audio Manipulation:** `ffmpeg`
*   **RSS Parsing:** `feedparser`
*   **HTTP Requests:** `requests`
*   **Database:** SQLite
*   **Containerization:** Docker, Docker Compose
*   **Web Server:** Nginx

## Getting Started

### Local Development (Without Docker)

To get started with Podslice locally, you'll need Python 3.8+ and pip.

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
    Before running the application, you must set the environment variables listed in the **Environment Variables** section below. You can create a `.env` file in the project root to store these variables.

5.  **Run the Application:**
    ```bash
    python manage.py runserver
    ```
    This command starts the Django development server.

### Docker Deployment

The recommended way to run Podslice is using Docker and Docker Compose.

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/prashmohan/podslice.git
    cd podslice
    ```

2.  **Create an environment file:**
    Create a `.env` file by copying the example and filling in the required values.
    ```bash
    cp .env.example .env
    ```
    See the **Environment Variables** section for more details on each variable.

3.  **Build and run with Docker Compose:**
    ```bash
    docker-compose up --build
    ```
    This command will build the Docker image and start the `app` and `web` (Nginx) services. The application will be accessible at `http://localhost:12343`.

## Environment Variables

The following environment variables are used to configure the application. They can be placed in a `.env` file in the project root.

| Variable                      | Description                                                                                                   | Default                               |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `DJANGO_SECRET_KEY`           | A unique secret key for your Django project.                                                                  | A randomly generated key              |
| `GEMINI_API_KEY`              | Your API key for Google Generative AI.                                                                        | **Required**                          |
| `GEMINI_MODEL`                | The Gemini model to use for analysis.                                                                         | `gemini-1.5-pro`                      |
| `REHOST_BASE_URL`             | The base URL where your re-hosted media will be served (e.g., `http://localhost:12343`).                       | **Required**                          |
| `DJANGO_ALLOWED_HOSTS`        | A comma-separated list of allowed hostnames.                                                                  | `localhost,127.0.0.1`                 |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | A comma-separated list of trusted origins for CSRF.                                                           | `http://localhost:12343`              |
| `SECURE_SSL_REDIRECT`         | If `True`, redirects all HTTP requests to HTTPS.                                                              | `False`                               |
| `MAX_EPISODES_PER_PODCAST`    | The default maximum number of recent episodes to download and process for new podcast subscriptions. Can be overridden per podcast. | `10`                                  |
| `PODCAST_POLLING_INTERVAL`    | The interval, in seconds, at which to poll for new podcast episodes.                                          | `43200` (12 hours)                    |
| `LOG_LEVEL`                   | The logging level for the application.                                                                        | `INFO`                                |

## Nginx Configuration

The provided `nginx.conf` sets up a reverse proxy.

*   **Port `12343`:** This is the main port for the web interface and API. It proxies requests to the Django application running on port `8000`. It also serves static and media files directly.
*   **Port `12341`:** This port is dedicated to serving the re-hosted RSS feeds and media files. This allows for a separate, cleaner URL for podcast clients.

## Database Migrations

After making changes to models, you'll need to create and apply migrations.

```bash
python manage.py makemigrations
python manage.py migrate
```
If you are running the application with Docker, you will need to run these commands inside the `app` container:
```bash
docker-compose exec app python manage.py makemigrations
docker-compose exec app python manage.py migrate
```

## Running Tests

To run the tests for the `podcasts` app:

```bash
python manage.py test podcasts
```
To run tests inside the Docker container:
```bash
docker-compose exec app python manage.py test podcasts
```

## Code Style and Conventions

*   Follow Django's coding style.
*   Keep models, views, and serializers in their respective files.
*   Use Python's `threading` module for any long-running tasks to avoid blocking web requests.
*   Write tests for new features and bug fixes.
