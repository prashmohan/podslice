# Podslice: Engineering Design Document

**Author:** Prashanth Mohan
**Date:** June 17, 2025

---

## 1. Introduction & Vision

### 1.1. Problem Statement

Podcast listeners often face two primary issues: the disruption of advertisements within audio content and the potential for podcasts to become unavailable if the original host removes them. There is a need for a simple, reliable tool that allows a user to archive a podcast and enjoy an uninterrupted, ad-free listening experience.

### 1.2. Vision & Goals

The vision for Podslice is to be a simple, fire-and-forget service that transforms a standard podcast feed into a clean, personal, and permanent ad-free version.

The primary goals are:
* **Ad-Free Experience:** To programmatically identify and remove advertisement segments from podcast episodes.
* **Content Persistence:** To re-host audio content, providing the user with a durable link that they control.
* **Simplicity:** To provide a straightforward API-driven workflow with no complex user interface or account management.
* **Reliability:** To build a robust, fault-tolerant system that can handle long-running processes and recover from transient errors.

### 1.3. Non-Goals

To maintain focus and simplicity, this project will explicitly **not** include:
* User accounts or authentication.
* A graphical user interface (GUI). This is an API-first service.
* Monetization features or subscription management.
* Direct audio streaming; the service provides a new RSS feed for use in standard podcast clients.
* Support for video podcasts.

### 1.4. Guiding Principles

* **Simplicity Over Complexity:** We will always prefer simpler, self-contained solutions over those that introduce external dependencies unless absolutely necessary. The choice of SQLite and a filesystem message broker are direct results of this principle.
* **Decoupled Architecture:** Services should be independent and communicate via well-defined interfaces. The separation of `podslice_core` and `podslice_rehost` embodies this.
* **Asynchronous Processing:** The system must remain responsive. Any long-running operation (>500ms) must be offloaded to a background process.
* **Idempotency and Fault Tolerance:** Tasks should be designed to be safely retried without creating duplicate data or unintended side effects.

---

## 2. User Journeys & Requirements

### 2.1. Persona: The Technical Podcast Enthusiast

Our primary user, "Alex," is a technically savvy podcast listener. Alex is comfortable with APIs, wants to archive their favorite shows, and finds ad interruptions disruptive to their listening flow. They value control and permanence over their media.

### 2.2. The "Happy Path" Journey

1.  **Submission:** Alex finds the RSS feed for a podcast they love. Using a simple cURL command or a script, they make a `POST` request to the Podslice API (`/api/podcasts/`), providing the feed URL. The API immediately responds with a `202 Accepted` status and a unique ID for the new podcast job.
2.  **Processing (In Background):** The Podslice service begins its work. It fetches the feed, identifies all episodes, and queues a processing job for each one.
3.  **Polling for Status:** While the jobs are running, Alex can periodically make a `GET` request to the status endpoint (`/api/podcasts/<id>/status/`). The response shows the overall progress and the status of each individual episode (`PENDING`, `DOWNLOADING`, `COMPLETED`, `FAILED`).
4.  **Completion:** After some time (depending on the number and length of episodes), the status endpoint shows that all episodes are `COMPLETED`.
5.  **Subscription:** Alex takes the new feed URL provided by the service (`/feeds/podcasts/<id>/rss.xml`) and adds it to their favorite podcast client (e.g., Overcast, Pocket Casts).
6.  **Listening:** The podcast appears in their client. When they play an episode, they enjoy an uninterrupted, ad-free experience, served seamlessly from the Podslice re-hosting service.

### 2.3. Error & Edge Case Journeys

* **Invalid URL Submission:** Alex accidentally provides a malformed URL or a URL that doesn't point to a valid RSS feed. The API should immediately reject the request with a `400 Bad Request` error and a clear message.
* **Episode Download Failure:** An episode's audio file is missing from the original server (404 error). The Celery worker should mark that specific episode as `FAILED` in the database, log the error, and move on to the next episode. The overall podcast job should still complete, providing a feed of the successfully processed episodes.
* **AI Service Failure:** The Gemini API is temporarily unavailable or returns an error. The Celery task should implement a retry mechanism with exponential backoff. If it ultimately fails after several retries, the episode is marked as `FAILED`.
* **Audio Without Ads:** The AI service analyzes an episode and finds no ads. The system should gracefully handle this by skipping the audio processing step and simply re-hosting the original, unchanged audio file.

---

## 3. System Architecture & Design

### 3.1. High-Level Architecture

The architecture is composed of a single monolithic Django service, a shared file store, and a background worker system. This simplifies deployment and development by keeping all components in a single project.
```
      User Request (RSS URL)
            |
            v
+--------------------------------------+
|      podslice_core (Django/DRF)      |
|       - API, Serves new Feed         |
|       - Serves rehosted media        |
+--------------------------------------+
|         ^           |
| R/W     | R         | (1) Enqueues Task
v         |           v
+----------------+   +-----------------------------+
| SQLite DB      |   | Celery Filesystem Broker    |
|                |   |  (/celery_broker directory) |
+----------------+   +-----------------------------+
                                  |
                                  | (2) Worker Pulls Task
                                  v
+--------------------------------------------------------------------------+
|                              Celery Worker                               |
|--------------------------------------------------------------------------|
| 3. Downloads original_audio_url                                          |
| 4. Sends audio to Google Gemini API -> gets ad timestamps                |
| 5. Uses pydub to slice audio, removes ad segments                        |
| 6. Saves new ad-free audio to /media storage                             |
| 7. Updates SQLite DB with status and media metadata                      |
+--------------------------------------------------------------------------+

```

### 3.2. Component Deep Dive

* **`podslice_core` (The Monolith):** This Django project is the user's sole point of interaction. It validates input, manages the state of all processing jobs, orchestrates the background workers, and serves the final rehosted media files.

* **Asynchronous Task Processing:** We use Celery with the filesystem broker. **Justification:** This is a cornerstone of the "simplicity" principle. It removes the need to install, configure, and maintain a separate service like Redis or RabbitMQ. For the expected workload of this service, the performance of the filesystem broker is more than sufficient and its operational simplicity is a major advantage.

* **Data Persistence:** We use a single SQLite database. **Justification:** This reinforces the monolithic nature of the architecture. SQLite is chosen for its zero-configuration, serverless nature, which perfectly aligns with the project's goal of being a simple, self-contained application.

### 3.3. Detailed Process Flow & Sequence Diagram

This diagram illustrates the precise flow of control and data between the components.

```
User          podslice_core API     Celery Broker        Celery Worker         Gemini API
|                    |                    |                    |                    |
+--POST /podcasts--->|                    |                    |                    |
|                    +--Create Podcast--->|                    |                    |
|                    +--process_feed.delay()------------------->|                    |
|<--202 Accepted----+                    |                    |                    |
|                    |                    +----Pulls Task----->|                    |
|                    |                    |                    +--Fetch/Parse RSS-->|
|                    |                    |                    +-rehost_audio.delay()------------------>|
|                    |                    |                    |                    |
|                    |                    |                    +----Pulls Task----->|
|                    |                    |                    |                    +---Analyze Audio-->|
|                    |                    |                    |                    |<----Timestamps----+
|                    |                    |                    +--Process w/ pydub->|
|                    |                    |                    +--Write to DB------>|
|                    |                    |                    |                    |
+--GET /feeds/id---->|                    |                    |                    |
|<----New RSS Feed---+                    |                    |                    |
|                    |                    |                    |                    |
| (Podcast Client)   |                    |                    |                    |
+--GET /media/id---->|                    |                    |                    |
|<----Audio File-----+                    |                    |                    |
```
## 4. Data Models & Schemas

### 4.1. `podslice_core` Database Schema (`db.sqlite3`)

* **`Podcast` model:**
    * `id` (UUIDField, Primary Key): A unique, non-sequential identifier.
    * `title` (CharField): The title of the podcast, extracted from the feed.
    * `rss_feed_url` (URLField, unique): The original URL submitted by the user.
    * `status` (CharField): The overall status of the podcast job. Choices: `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`.
    * `created_at`, `updated_at` (DateTimeField): For tracking and debugging.

* **`Episode` model:**
    * `id` (UUIDField, Primary Key).
    * `podcast` (ForeignKey to `Podcast`): Links the episode to its parent podcast.
    * `title` (CharField): The episode title.
    * `original_audio_url` (URLField): The original source URL of the audio file.
    * `rehosted_audio_url` (URLField, nullable): The final public URL pointing to the `podslice_rehost` server.
    * `ad_segments` (JSONField, nullable): Stores the list of ad timestamps from Gemini. E.g., `[{"start": 60.5, "end": 95.0}, {"start": 1800.2, "end": 1830.0}]`.
    * `status` (CharField): The status of the individual episode job. Choices: `PENDING`, `DOWNLOADING`, `ANALYZING`, `PROCESSING`, `COMPLETED`, `FAILED`.
    * `created_at`, `updated_at` (DateTimeField).

* **`RehostedMedia` model:**
    * `media_guid` (UUIDField, Primary Key): The unique identifier used in the public URL.
    * `file_path` (FilePathField): The absolute path to the processed audio file on the shared `/media` volume.
    * `content_type` (CharField): The MIME type of the audio file (e.g., `audio/mpeg`).

---

## 5. Testing Strategy

A robust testing strategy is non-negotiable. We will employ a multi-layered approach.

### 5.1. Unit Tests

* **Models:** Test model creation, validation logic, and custom methods.
* **Serializers:** Test validation, including handling of invalid URLs.
* **Tasks:** Test the core logic of each Celery task in isolation, mocking out external dependencies like `requests.get`, the Gemini API client, and database connections.

### 5.2. Integration Tests

* **API to Celery:** Test that a successful API call correctly creates a `Podcast` object and enqueues the `process_podcast_feed` task with the right parameters.
* **Worker to Databases:** Test that the Celery worker can successfully read from the `podslice_core` database and write to *both* the `core` and `rehost` databases within a single task.

### 5.3. End-to-End (E2E) Tests

* **Happy Path E2E Test:** A single test script that:
    1.  Submits a URL to a known, valid, simple RSS feed.
    2.  Polls the status endpoint until the status is `COMPLETED`.
    3.  Fetches the new RSS feed and validates its structure.
    4.  Extracts the `rehosted_audio_url` for one episode.
    5.  Makes a `GET` request to that URL and asserts that it receives a `200 OK` response with the correct `Content-Type` header.

### 5.4. Edge Case Testing Plan

* **Invalid Feeds:** Create a suite of static, invalid feed files (malformed XML, missing required tags, etc.) and test that the system handles them gracefully.
* **Network Failures:** Use libraries like `requests-mock` to simulate network errors (404s, 500s) from both the original audio source and the Gemini API. Verify that the tasks enter a `FAILED` state and log the appropriate error.
* **Large Files:** Test with an unusually large audio file (e.g., >500MB) to ensure there are no memory or timeout issues during download and processing.
* **No Ads Found:** Test with an audio file known to have no ads and verify that the `ad_segments` field remains empty and the file is re-hosted without modification.

---

## 6. Detailed Project Plan & Execution

This project is broken down into four distinct, executable phases.

### Phase 1: Foundation & Core Service (`podslice_core`)

* **Goal:** Establish the main application, its data models, and the API for job submission.
* **Tasks:**
    1.  **Setup `podslice_core` Project:** Initialize the Django project and the `podcasts` app. Configure the SQLite database.
    2.  **Define Models:** Implement the `Podcast` and `Episode` models in `podcasts/models.py`. Generate and run the initial migration.
    3.  **Setup Celery:** Configure Celery with the filesystem broker in `settings.py`. Create `celery.py` and modify `__init__.py`.
    4.  **Implement Submission API:** Create the serializer and view for the `POST /api/podcasts/` endpoint. Initially, it will only create the `Podcast` object.
    5.  **Implement Stub Task:** Create the `process_podcast_feed` task, but have it only parse the feed and create `Episode` objects without dispatching sub-tasks.
    6.  **Connect API to Task:** Wire the submission API to enqueue the `process_podcast_feed` task.

### Phase 2: Re-hosting Service & File Serving (`podslice_rehost`)

* **Goal:** Build the independent service responsible for serving the final audio files.
* **Tasks:**
    1.  **Setup `podslice_rehost` Project:** Initialize the Django project and the `rehost_app`.
    2.  **Define Re-host Model:** Implement the `RehostedMedia` model and run its migration.
    3.  **Implement Serving View:** Create the view that takes a `media_guid`, looks up the file path in its database, and returns a `FileResponse`.
    4.  **Configure URLs:** Set up the URL pattern to route requests to the serving view.

### Phase 3: The Worker Pipeline (The "Magic")

* **Goal:** Implement the full, multi-step audio processing logic within the Celery worker.
* **Tasks:**
    1.  **Implement `rehost_episode_audio` Task:** Create the new Celery task.
    2.  **Audio Download:** Add logic to download the audio file from `original_audio_url`.
    3.  **Database Bridge:** Implement the logic for the worker to connect to the `podslice_rehost` SQLite database. This will require careful configuration management to pass the database path.
    4.  **Gemini Integration:** Write the client code to send the audio file to the Gemini API and parse the timestamp response. Securely manage the API key.
    5.  **Audio Slicing:** Use `pydub` to implement the ad removal based on the Gemini timestamps. Add logic to handle cases where no ads are found.
    6.  **Finalize and Connect:** Update the `process_podcast_feed` task to dispatch the `rehost_episode_audio` task for each episode.

### Phase 4: Finalization, Feeds & Status

* **Goal:** Expose the results of the processing to the user.
* **Tasks:**
    1.  **Implement RSS Feed View:** Create the view at `/feeds/podcasts/<id>/rss.xml`. This view will query the database for the completed episodes and render a valid RSS XML response.
    2.  **Implement Status API:** Create the `GET /api/podcasts/<id>/status/` endpoint, which will serialize the `Podcast` object and its related `Episode` objects to show detailed progress.
    3.  **Testing and Validation:** Execute the full testing strategy outlined in Section 5.
    4.  **Documentation:** Write a comprehensive `README.md` detailing the setup, configuration, and API usage for the entire system.
