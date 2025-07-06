# Podslice: Engineering Design Document

**Author:** Prashanth Mohan
**Date:** July 6, 2025

---

## 1. Introduction & Vision

### 1.1. Problem Statement

Podcast listeners often face two primary issues: the disruption of advertisements within audio content and the potential for podcasts to become unavailable if the original host removes them. There is a need for a simple, reliable tool that allows a user to archive a podcast and enjoy an uninterrupted, ad-free listening experience.

### 1.2. Vision & Goals

The vision for Podslice is to be a simple, fire-and-forget service that transforms a standard podcast feed into a clean, personal, and permanent ad-free version.

The primary goals are:
* **Ad-Free Experience:** To programmatically identify and remove advertisement segments from podcast episodes.
* **Content Persistence:** To re-host audio content, providing the user with a durable link that they control.
* **Simplicity:** To provide a straightforward web interface and API-driven workflow.
* **Reliability:** To build a robust, fault-tolerant system that can handle long-running processes and recover from transient errors.

### 1.3. Non-Goals

To maintain focus and simplicity, this project will explicitly **not** include:
* User accounts or authentication.
* Monetization features or subscription management.
* Direct audio streaming; the service provides a new RSS feed for use in standard podcast clients.
* Support for video podcasts.

### 1.4. Guiding Principles

* **Simplicity Over Complexity:** We will always prefer simpler, self-contained solutions over those that introduce external dependencies unless absolutely necessary.
* **Monolithic Architecture:** All components are part of a single Django project to simplify development and deployment.
* **Asynchronous Processing:** Any long-running operation (>500ms) must be offloaded to a background process to keep the application responsive.
* **Idempotency and Fault Tolerance:** Tasks should be designed to be safely retried without creating duplicate data or unintended side effects.

---

## 2. User Journeys & Requirements

### 2.1. Persona: The Podcast Enthusiast

Our primary user, "Alex," is a podcast listener who wants to archive their favorite shows and finds ad interruptions disruptive to their listening flow. They value control and permanence over their media.

### 2.2. The "Happy Path" Journey

1.  **Subscription:** Alex visits the Podslice web interface, enters the RSS feed URL for a podcast they love, and clicks "Subscribe".
2.  **Processing (In Background):** The Podslice service begins its work. It fetches the feed, identifies all episodes, and queues a processing job for each one in a background thread.
3.  **Viewing Status:** Alex is redirected to a status page where they can see the progress of the podcast's episodes. The page shows the status of each individual episode (`NEW`, `QUEUED`, `DOWNLOADING`, `ANALYZING`, `PROCESSING`, `COMPLETE`, `FAILED`).
4.  **Completion:** After some time (depending on the number and length of episodes), the status page shows that all episodes are `COMPLETE`.
5.  **Listening:** Alex can then use the new, re-hosted RSS feed in their favorite podcast client. When they play an episode, they enjoy an uninterrupted, ad-free experience, served seamlessly from the Podslice service.

### 2.3. Error & Edge Case Journeys

* **Invalid URL Submission:** Alex accidentally provides a malformed URL or a URL that doesn't point to a valid RSS feed. The web interface should show an error message.
* **Episode Download Failure:** An episode's audio file is missing from the original server (404 error). The background thread should mark that specific episode as `FAILED` in the database, log the error, and move on to the next episode. The overall podcast job should still complete, providing a feed of the successfully processed episodes.
* **AI Service Failure:** The Gemini API is temporarily unavailable or returns an error. The processing task should implement a retry mechanism. If it ultimately fails, the episode is marked as `FAILED`.
* **Audio Without Ads:** The AI service analyzes an episode and finds no ads. The system should gracefully handle this by skipping the audio processing step and simply re-hosting the original, unchanged audio file.

---

## 3. System Architecture & Design

### 3.1. High-Level Architecture

The architecture is a single monolithic Django application. This simplifies deployment and development by keeping all components in a single project. Long-running tasks are handled by background threads managed by a `ThreadPoolExecutor`.

```
      User Request (RSS URL)
            |
            v
+--------------------------------------+
|      podslice (Django/DRF)           |
|       - Web UI, API, Feeds           |
|       - Serves rehosted media        |
+--------------------------------------+
|         ^           |
| R/W     | R         | (1) Spawns Background Thread
v         |           v
+----------------+   +-----------------------------+
| SQLite DB      |   | ThreadPoolExecutor          |
|                |   |                             |
+----------------+   +-----------------------------+
                                  |
                                  | (2) Thread Pulls Task
                                  v
+--------------------------------------------------------------------------+
|                          Background Thread                               |
|--------------------------------------------------------------------------|
| 3. Downloads original_audio_url                                          |
| 4. Sends audio to Google Gemini API -> gets ad timestamps                |
| 5. Uses ffmpeg to slice audio, removes ad segments                       |
| 6. Saves new ad-free audio to /media storage                             |
| 7. Updates SQLite DB with status and media metadata                      |
+--------------------------------------------------------------------------+

```

### 3.2. Component Deep Dive

* **`podslice` (The Monolith):** This Django project is the user's sole point of interaction. It provides the web interface, validates input, manages the state of all processing jobs, orchestrates the background threads, and serves the final rehosted media files.

* **Asynchronous Task Processing:** We use Python's built-in `threading` module and a `ThreadPoolExecutor`. **Justification:** This is a cornerstone of the "simplicity" principle. It removes the need to install, configure, and maintain a separate service like Redis or RabbitMQ. For the expected workload of this service, the performance of background threads is more than sufficient and its operational simplicity is a major advantage.

* **Data Persistence:** We use a single SQLite database. **Justification:** This reinforces the monolithic nature of the architecture. SQLite is chosen for its zero-configuration, serverless nature, which perfectly aligns with the project's goal of being a simple, self-contained application.

### 3.3. Detailed Process Flow & Sequence Diagram

This diagram illustrates the precise flow of control and data between the components.

```
User          Podslice Web/API        Background Thread    Gemini API
|                    |                    |                    |
+--POST /subscribe/ ->|                    |                    |
|                    +--Create Podcast--->|                    |
|                    +--poll_feed()------->|                    |
|<--Redirect/200 OK--+                    |                    |
|                    |                    +--Fetch/Parse RSS-->|
|                    |                    +-rehost_audio()----->|
|                    |                    |                    +---Analyze Audio-->|
|                    |                    |                    |<----Timestamps----+
|                    |                    +--Process w/ ffmpeg>|
|                    |                    +--Write to DB------>|
|                    |                    |                    |
+--GET /feeds/id---->|                    |                    |
|<----New RSS Feed---+                    |                    |
|                    |                    |                    |
| (Podcast Client)   |                    |                    |
+--GET /media/id---->|                    |                    |
|<----Audio File-----+                    |                    |
```
## 4. Data Models & Schemas

### 4.1. Database Schema (`db.sqlite3`)

* **`Podcast` model:**
    * `id` (UUIDField, Primary Key): A unique, non-sequential identifier.
    * `title` (CharField): The title of the podcast, extracted from the feed.
    * `slug` (SlugField): URL-friendly slug.
    * `rss_url` (URLField, unique): The original URL submitted by the user.
    * `artwork_url` (URLField): URL for the podcast artwork.
    * `last_polled` (DateTimeField): When the feed was last checked for new episodes.

* **`Episode` model:**
    * `id` (UUIDField, Primary Key).
    * `podcast` (ForeignKey to `Podcast`): Links the episode to its parent podcast.
    * `guid` (CharField): Unique ID for the episode from the RSS feed.
    * `title` (CharField): The episode title.
    * `pub_date` (DateTimeField): Publication date of the episode.
    * `original_audio_url` (URLField): The original source URL of the audio file.
    * `rehosted_audio_url` (URLField, nullable): The final public URL pointing to the server.
    * `rehosted_audio_size` (BigIntegerField): Size of the rehosted audio file in bytes.
    * `rehosted_media_id` (UUIDField): ID of the rehosted media in the `RehostedMedia` table.
    * `ad_segments` (JSONField, nullable): Stores the list of ad timestamps from Gemini. E.g., `[{"start": 60.5, "end": 95.0}]`.
    * `status` (CharField): The status of the individual episode job. Choices: `NEW`, `QUEUED`, `DOWNLOADING`, `ANALYZING`, `PROCESSING`, `COMPLETE`, `FAILED`.

* **`RehostedMedia` model:**
    * `media_guid` (UUIDField, Primary Key): The unique identifier used in the public URL.
    * `file_path` (CharField): The absolute path to the processed audio file on the shared `/media` volume.
    * `content_type` (CharField): The MIME type of the audio file (e.g., `audio/mpeg`).

---

## 5. Testing Strategy

A robust testing strategy is non-negotiable. We will employ a multi-layered approach.

### 5.1. Unit Tests

* **Models:** Test model creation, validation logic, and custom methods.
* **Serializers:** Test validation, including handling of invalid URLs.
* **Tasks:** Test the core logic of each background task in isolation, mocking out external dependencies like `requests.get`, the Gemini API client, and database connections.

### 5.2. Integration Tests

* **API to Background Thread:** Test that a successful API call correctly creates a `Podcast` object and starts the `poll_feed` task with the right parameters.
* **Thread to Databases:** Test that the background thread can successfully read from and write to the database.

### 5.3. End-to-End (E2E) Tests

* **Happy Path E2E Test:** A single test script that:
    1.  Submits a URL to a known, valid, simple RSS feed.
    2.  Polls the status endpoint until the status is `COMPLETED`.
    3.  Fetches the new RSS feed and validates its structure.
    4.  Extracts the `rehosted_audio_url` for one episode.
    5.  Makes a `GET` request to that URL and asserts that it receives a `200 OK` response with the correct `Content-Type` header.