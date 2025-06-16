# Engineering Design Document: PodSlice

**Version:** Final 2.0 (Elaborated)
**Date:** June 16, 2025
**Author:** Prashanth Mohan

## 1. Overview

This document provides a comprehensive and highly specific engineering design for "PodSlice," a self-hosted system for creating ad-free versions of podcasts. The system is composed of two distinct microservices:

1.  **`PodSlice-Core`**: A user-facing service for managing podcast subscriptions and orchestrating the audio processing pipeline.
2.  **`PodSlice-Rehost`**: A headless service that consumes processed episode data and serves the ad-free RSS feeds and audio files.

The design strictly adheres to a philosophy of simplicity and self-containment, using the local filesystem for all storage and minimizing external dependencies. Every component, function, and configuration is detailed to eliminate confusion and provide a clear path for implementation.

## 2. System Architecture

The architecture is built on two decoupled services that communicate asynchronously via a message queue and a shared filesystem, ensuring resilience and modularity.

**Architectural Diagram:**

```
+-----------------+      (1) HTTP      +---------------------+      (2) API       +-------------------------+
|  User's Browser |<------------------>|    PodSlice-Core    |<------------------|   Celery Workers        |
| (UI for Mgmt)   |                    | (Django/Gunicorn)   |                    | (Audio Processing)      |
+-----------------+                    +----------+----------+                    +------------+------------+
                                                  | (DB)                             (5) Write |
                                                  |                                  Processed |
                                       +----------v----------+                          File |
                                       |  Core DB (SQLite)   |                               v
                                       +------------------------------------------------------+
                                       |             Shared Local Filesystem                  |
                                       |         (e.g., /podslice-media/)                     |
                                       |    (Mounted as a volume in both services)            |
                                       +------------------------------------------------------+
                                                                                             ^
                                                                                             | (7) Read File
                                                                                             |
+---------------------+      (8) HTTP GET      +---------------------+      (6) Process     +-------------------------+
| Podcast Player App  |<---------------------|  PodSlice-Rehost    |<----- Message ----|   Message Queue (Redis) |<-----(4) Publish Msg
| (Consuming new feed)|                      | (FastAPI/Uvicorn)   |                    |  (e.g., "episode_done") |      |
+---------------------+                      +----------+----------+                    +-------------------------+      |
                                                        | (DB)                                                            |
                                             +----------v----------+                                                      |
                                             | Rehost DB (SQLite)  |------------------------------------------------------+
                                             +---------------------+
```

**Detailed Process Flow:**
1.  **User Interaction:** A user interacts with the `PodSlice-Core` web UI (HTML served by Django).
2.  **API Call:** Actions (e.g., adding a podcast) trigger API calls to the Django backend.
3.  **Task Offloading:** The Django view validates the request, creates a `Podcast` record in its local SQLite DB, and then enqueues a processing task for Celery (e.g., `poll_feed.delay(podcast_id)`). The task message is sent to a Redis queue.
4.  **Message Publishing:** A separate Celery worker process, monitoring the Redis queue, executes the task. Upon successful completion of the multi-step audio processing, the worker constructs a detailed JSON message. This message is then published to a Redis Pub/Sub channel named `episode_processed_channel`.
5.  **File Storage:** During processing, the Celery worker writes the final, ad-free audio file to a pre-defined location on a shared filesystem volume (e.g., `/media/processed/my-podcast-slug/episode-guid.mp3`).
6.  **Message Consumption:** The `PodSlice-Rehost` service runs a dedicated consumer process that is subscribed to the `episode_processed_channel`. It receives the JSON message, parses it, and writes the metadata to its own independent SQLite database (`rehost.db`).
7.  **File Serving:** The `PodSlice-Rehost` service runs a FastAPI web server that reads from the shared filesystem volume.
8.  **Feed Consumption:** A third-party podcast player app sends a GET request to a `PodSlice-Rehost` URL (e.g., `http://rehost.podslice.local/feeds/my-podcast-slug.xml`). `PodSlice-Rehost` dynamically generates the RSS XML, including `<enclosure>` tags with URLs pointing to its own file-serving endpoint (e.g., `http://rehost.podslice.local/audio/processed/my-podcast-slug/episode-guid.mp3`). The player then follows this URL to download the ad-free audio.

## 3. Shared Infrastructure and Communication Protocol

### 3.1. Shared Local Filesystem
*   **Host Path:** `/var/data/podslice-media`
*   **Permissions:** The host directory must be writable by the user/group ID that the containerized services run as. `mkdir -p /var/data/podslice-media && sudo chown -R 1000:1000 /var/data/podslice-media` (assuming services run as user ID 1000).
*   **Container Mount Point:** `/media`. This path must be identical in the `podslice_core_celery` and `podslice_rehost` services.
*   **Directory Structure (Managed by the application):**
    *   `/media/original/`: Stores unmodified downloaded audio. Can be periodically purged.
    *   `/media/processed/`: Stores the final ad-free audio files. This is the source for the Rehost service.

### 3.2. Message Queue: Redis Pub/Sub
*   **Service Name (in Docker Compose):** `redis`
*   **URL (for application configuration):** `redis://redis:6379/0`
*   **Channel Name:** `episode_processed_channel`
*   **Message Schema (JSON):** This is the strict contract between the two services.

```json
{
  "podcast": {
    "original_rss_url": "string (The source RSS feed URL)",
    "slug": "string (URL-friendly version of the podcast title, e.g., 'the-daily')",
    "title": "string (The full podcast title)",
    "author": "string (The podcast author)",
    "artwork_url": "string (URL to the podcast's cover art)",
    "description": "string (The full description of the podcast)"
  },
  "episode": {
    "guid": "string (The unique episode identifier from the source RSS)",
    "title": "string (The full episode title)",
    "pub_date_iso": "string (The publication date as an ISO 8601 string, e.g., '2023-10-26T10:00:00+00:00')",
    "description": "string (The episode's show notes/description)",
    "absolute_file_path": "string (The absolute path to the processed file *within the container*, e.g., '/media/processed/the-daily/xyz-123.mp3')",
    "duration_seconds": "integer (The duration of the final ad-free audio)",
    "audio_file_size_bytes": "integer (The file size of the final ad-free audio)"
  }
}
```

## 4. `PodSlice-Core` Service: Detailed Design

### 4.1. Configuration
*   **Environment Variables:**
    *   `SECRET_KEY`: Django's secret key.
    *   `CELERY_BROKER_URL`: e.g., `redis://redis:6379/0`
    *   `GEMINI_API_KEY`: The API key for Google Gemini.
    *   `MEDIA_ROOT`: Absolute path to the shared volume, e.g., `/media`
*   **`podslice_core/settings.py` (Key Snippets):**
    ```python
    # Application definition
    INSTALLED_APPS = [
        # ... django apps
        'rest_framework',
        'podcasts',
    ]

    # Celery Configuration
    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = CELERY_BROKER_URL
    ```

### 4.2. Data Models (`podcasts/models.py`)
The models are the source of truth for the processing state.

```python
from django.db import models
from django.utils.text import slugify

class Podcast(models.Model):
    title = models.CharField(max_length=255, help_text="Title of the podcast, parsed from feed.")
    slug = models.SlugField(max_length=255, unique=True, help_text="URL-friendly slug, generated from title.")
    rss_url = models.URLField(unique=True, help_text="The unique URL of the podcast's RSS feed.")
    artwork_url = models.URLField(blank=True, null=True)
    # ... other metadata fields

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

class Episode(models.Model):
    class Status(models.TextChoices):
        NEW = 'NEW', 'New'
        QUEUED = 'QUEUED', 'Queued'
        DOWNLOADING = 'DOWNLOADING', 'Downloading'
        TRANSCRIBING = 'TRANSCRIBING', 'Transcribing'
        IDENTIFYING_ADS = 'IDENTIFYING_ADS', 'Identifying Ads'
        PROCESSING_AUDIO = 'PROCESSING_AUDIO', 'Processing Audio'
        COMPLETE = 'COMPLETE', 'Complete'
        FAILED = 'FAILED', 'Failed'

    podcast = models.ForeignKey(Podcast, on_delete=models.CASCADE, related_name='episodes')
    guid = models.CharField(max_length=512, unique=True, help_text="Unique episode ID from RSS feed.")
    title = models.CharField(max_length=255)
    pub_date = models.DateTimeField()
    original_audio_url = models.URLField(max_length=1024)
    # Stores the path *inside the container*
    original_audio_path = models.CharField(max_length=512, blank=True, null=True)
    processed_audio_path = models.CharField(max_length=512, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    failure_reason = models.TextField(blank=True, null=True)
```

### 4.3. API Endpoints
*   **`POST /api/podcasts/`**
    *   **Action:** Subscribes to a new podcast.
    *   **Request Body:** `{"rss_url": "http://example.com/feed.xml"}`
    *   **Success Response (201 Created):**
        ```json
        {
          "id": 1,
          "title": "Example Podcast Title",
          "slug": "example-podcast-title",
          "rss_url": "http://example.com/feed.xml",
          "artwork_url": "http://example.com/art.jpg"
        }
        ```
    *   **Error Responses:**
        *   `400 Bad Request (Validation Error)`: `{"rss_url": ["Enter a valid URL."]}`
        *   `400 Bad Request (Already Exists)`: `{"rss_url": ["Podcast with this Rss url already exists."]}`
        *   `400 Bad Request (Feed Unreachable)`: `{"non_field_errors": ["Could not fetch or parse the feed at the provided URL."]}`

### 4.4. Celery Tasks (`podcasts/tasks.py`)

*   **`poll_all_feeds()` (Scheduled)**
    *   **Logic:**
        1.  Fetches all `Podcast` objects from the DB.
        2.  For each podcast, `feedparser.parse(podcast.rss_url)`.
        3.  For each `entry` in the parsed feed:
            a. Get the `guid` (e.g., `entry.id`).
            b. Check if an `Episode` with this `guid` already exists.
            c. If not, create a new `Episode` record with `status='NEW'`.
            d. Dispatch the processing task: `process_episode.delay(new_episode.id)`.
            e. Update the new episode's status to `QUEUED`.

*   **`process_episode(episode_id)`**
    *   **Pseudo-code:**
        ```python
        # Fetch episode and publisher instance
        episode = Episode.objects.get(id=episode_id)
        publisher = RedisPublisher() # A helper class to publish messages

        try:
            # 1. DOWNLOAD
            episode.status = Episode.Status.DOWNLOADING
            episode.save()
            original_path = download_audio_file(episode.original_audio_url, episode.podcast.slug, episode.guid)
            episode.original_audio_path = original_path
            episode.save()

            # 2. TRANSCRIBE
            episode.status = Episode.Status.TRANSCRIBING
            episode.save()
            transcript = transcribe_audio(original_path) # Returns text with timestamps

            # 3. IDENTIFY ADS
            episode.status = Episode.Status.IDENTIFYING_ADS
            episode.save()
            ad_timestamps = find_ads_with_gemini(transcript)

            # 4. PROCESS AUDIO
            episode.status = Episode.Status.PROCESSING_AUDIO
            episode.save()
            processed_path, duration, size = remove_ads_and_save(original_path, ad_timestamps)
            episode.processed_audio_path = processed_path
            episode.save()

            # 5. PUBLISH & COMPLETE
            message = create_message_payload(episode, processed_path, duration, size)
            publisher.publish(message)
            episode.status = Episode.Status.COMPLETE
            episode.save()

        except Exception as e:
            episode.status = Episode.Status.FAILED
            episode.failure_reason = str(e)
            episode.save()
            # Log the full traceback
        ```
    *   **Error Handling:** Each helper function (`download_audio_file`, etc.) must be wrapped in its own `try...except` block to catch specific exceptions (`requests.RequestException`, file I/O errors, API client errors) and re-raise them as a custom exception that the main task can catch to set the `FAILED` status and log the reason.

## 5. `PodSlice-Rehost` Service: Detailed Design

### 5.1. Configuration
*   **Environment Variables:**
    *   `REDIS_URL`: e.g., `redis://redis:6379/0`
    *   `MEDIA_ROOT`: Absolute path to the shared volume, e.g., `/media`
    *   `DATABASE_PATH`: Absolute path to the database file, e.g., `/data/rehost.db`

### 5.2. Database Setup (`rehost/database.py`)

```python
import sqlite3
import os

DATABASE_PATH = os.environ.get("DATABASE_PATH", "/data/rehost.db")

def get_db_connection():
    # Ensure the directory exists
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    conn = get_db_connection()
    # SQL commands from Section 4.1
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS RehostedPodcast (...);
        CREATE TABLE IF NOT EXISTS RehostedEpisode (...);
    """)
    conn.commit()
    conn.close()
```

### 5.3. Message Consumer (`rehost/consumer.py`)
```python
import redis
import json
import time
from . import database

def run_consumer():
    r = redis.from_url(os.environ.get("REDIS_URL"))
    pubsub = r.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe("episode_processed_channel")

    while True:
        try:
            message = pubsub.get_message(timeout=5.0)
            if message is None:
                continue

            data = json.loads(message['data'])
            process_message(data)

        except redis.ConnectionError:
            time.sleep(5) # Wait before retrying
        except json.JSONDecodeError as e:
            # Log malformed message
            print(f"Error decoding JSON: {e}")
        except Exception as e:
            # Log other unexpected errors
            print(f"An error occurred: {e}")

def process_message(data):
    # Parameterized SQL to prevent injection
    podcast_sql = "INSERT OR IGNORE INTO RehostedPodcast (original_rss_url, slug, ...) VALUES (?, ?, ...);"
    episode_sql = "INSERT OR IGNORE INTO RehostedEpisode (guid, podcast_id, ...) VALUES (?, (SELECT id FROM RehostedPodcast WHERE slug=?), ...);"

    conn = database.get_db_connection()
    # Use data from the 'podcast' and 'episode' keys in the JSON message
    conn.execute(podcast_sql, (data['podcast']['original_rss_url'], ...))
    conn.execute(episode_sql, (data['episode']['guid'], data['podcast']['slug'], ...))
    conn.commit()
    conn.close()
```

### 5.4. API Endpoints (`rehost/main.py`)
```python
from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
import os
from . import database # For querying
from . import rss_generator # Helper to build XML

app = FastAPI()

# 1. Static File Serving Endpoint
# Mounts the shared directory '/media' to the URL '/audio'
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/media")
app.mount("/audio", StaticFiles(directory=MEDIA_ROOT), name="audio")

# 2. RSS Feed Generation Endpoint
@app.get("/feeds/{podcast_slug}.xml")
def get_podcast_feed(podcast_slug: str):
    conn = database.get_db_connection()
    podcast = conn.execute("SELECT * FROM RehostedPodcast WHERE slug = ?", (podcast_slug,)).fetchone()
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")

    episodes = conn.execute("SELECT * FROM RehostedEpisode WHERE podcast_id = ? ORDER BY pub_date DESC", (podcast['id'],)).fetchall()
    conn.close()

    # rss_generator.build_xml() uses xml.etree.ElementTree to construct the feed
    xml_content = rss_generator.build_xml(podcast, episodes)
    return Response(content=xml_content, media_type="application/xml")
```

## 6. Development Task Breakdown

| Task ID | Title                          | Description                                                                                                                                                                                                                                                        | Implementation Details & Snippets                                                                                                                                                                                            | Acceptance Criteria & Testing                                                                                                                                                                                                                                                                                           |
| :------ | :----------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1       | Core: Project & Model Setup      | Initialize the `podslice_core` Django project and `podcasts` app. Implement the `Podcast` and `Episode` models as specified in Section 4.2. Run migrations to create the database schema.                                                                        | **`podcasts/models.py`**: Use the code from Section 4.2. Include `Status` choices and the `save()` method override on `Podcast` to generate the slug.                                                                           | Run `python manage.py makemigrations` and `python manage.py migrate`. Verify the `db.sqlite3` file is created. Use the Django shell to create a `Podcast` object and verify its `slug` is auto-generated.                                                                                                                   |
| 2       | Core: Podcast Subscription API | Create a DRF Serializer and API View to handle `POST` requests for adding new podcast subscriptions. The view must validate the `rss_url`, parse the feed to get metadata, save the `Podcast` object, and dispatch an initial polling task.                         | **`podcasts/serializers.py`**: Create a `PodcastSerializer`. **`podcasts/views.py`**: In the `perform_create` method, wrap `feedparser.parse()` in a `try...except` block. If successful, populate `title`, `artwork_url`, etc., before `serializer.save()`. Call `poll_feed.delay(podcast.id)`. | Send a `POST` request to `/api/podcasts/` with a valid RSS URL. Assert a `201` response. Check the database to confirm the `Podcast` was created. Send a request with an invalid URL and assert a `400` response with a specific error message.                                                            |
| 3       | Core: Celery Processing Task   | Implement the full `process_episode` task in `podcasts/tasks.py`. Follow the pseudo-code from Section 4.4, creating helper functions for each step (download, transcribe, etc.). Implement robust status updates and exception handling that populates `failure_reason`. | **`podcasts/tasks.py`**: Create the main `process_episode` task. For the Gemini call, use a prompt like: `You are an expert audio editor. Identify ad segments in this transcript. Respond with JSON: {"advertisements": [{"start_time": s, "end_time": s}]}`. Use `pydub` to slice and concatenate audio segments. | Manually trigger the task for a `NEW` episode. Observe the `status` field changing in the database (e.g., via Django admin). For a successful run, confirm the status becomes `COMPLETE` and a file appears in `/media/processed/`. For a failed run, confirm the status is `FAILED` and `failure_reason` is populated. |
| 4       | Rehost: Scaffolding & DB       | Create the `podslice_rehost` FastAPI project. Implement the `database.py` script as specified in Section 5.2. Create an `init` command that can be run to create the database tables.                                                                                  | **`rehost/database.py`**: Use the code from Section 5.2. **`rehost/main.py`**: Add a startup event handler: `@app.on_event("startup") def startup_event(): database.initialize_database()`.                                        | Run `uvicorn rehost.main:app`. A `/data/rehost.db` file should be created with two empty tables (`RehostedPodcast`, `RehostedEpisode`). Verify the schema using the `sqlite3` CLI tool.                                                                                                                     |
| 5       | Rehost: Message Consumer       | Implement the `consumer.py` script as detailed in Section 5.3. It must connect to Redis, listen on the `episode_processed_channel`, parse incoming JSON messages, and use parameterized SQL to save the data to `rehost.db`.                                      | **`rehost/consumer.py`**: Use the code from Section 5.3. Ensure all SQL `INSERT` statements are `INSERT OR IGNORE` to handle message redelivery gracefully.                                                                       | Run the consumer in one terminal. In another, use `redis-cli` to `PUBLISH episode_processed_channel '...'` with a valid JSON payload from Section 3.2. Query `rehost.db` to confirm that new rows have been added to both tables.                                                                    |
| 6       | Core: Redis Publishing         | Modify the `process_episode` task in `podslice_core`. After the audio is successfully processed and saved, construct the JSON payload (as defined in Section 3.2) and use `redis-py` to `publish` it to the `episode_processed_channel`.                           | **`podcasts/tasks.py`**: At the end of the `try` block in `process_episode`, add: `import redis; r = redis.from_url(...); payload = {...}; r.publish('episode_processed_channel', json.dumps(payload))`                          | With the Rehost consumer running, process an episode in the Core service. The consumer's console output should show that it received and processed a message.                                                                                                                                                               |
| 7       | Rehost: Feed & File Endpoints  | Implement the two endpoints in `rehost/main.py` as detailed in Section 5.4. One for serving the dynamically generated XML feed, and one for serving static audio files from the shared `/media` volume.                                                               | **`rehost/main.py`**: Use the code from Section 5.4. For the XML generator, use Python's `xml.etree.ElementTree` to safely build the XML structure and avoid string formatting errors.                                                | Populate the `rehost.db` with sample data. Place a corresponding audio file in `/media/processed/`. `curl http://localhost:8001/feeds/test-slug.xml` should return valid XML. `curl http://localhost:8001/audio/processed/test.mp3` should download the file. |
| 8       | System: Dockerization          | Create `Dockerfile`s for both services and a `docker-compose.yml` file to orchestrate the entire system: `core_web`, `core_celery`, `rehost`, and `redis`. Configure a named volume and mount it correctly to the `core_celery` and `rehost` services.             | **`docker-compose.yml`**: `services: ... core_celery: volumes: - podslice_media:/media ... rehost: volumes: - podslice_media:/media ... volumes: podslice_media:`                                                         | Run `docker-compose up --build`. All four services should start without errors. The entire end-to-end flow should work: add a podcast via the UI, see it get processed, and be able to subscribe to the new feed from the Rehost service, which successfully serves the ad-free audio. |

