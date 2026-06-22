# Design Spec: Dynamic Episode Processing (On-Demand)

This document specifies the design for transitioning Podslice from an upfront download/processing model to an on-demand, dynamic processing model. 

---

## 1. Overview & Goals

Currently, when subscribing to a podcast or polling its RSS feed, Podslice immediately downloads and processes podcast episodes (sends them to the Gemini API and runs FFmpeg slicing). This wastes processing resources and API credits on episodes that may never be accessed by a client.

The new model will:
1. **Fetch metadata only**: Ingestion only pulls and stores the episode metadata.
2. **On-demand processing**: Audio download, Gemini analysis, and ad-splicing are deferred until the rehosted URL is actually accessed by a client device.
3. **Locking and queueing**: Ensure that multiple concurrent requests or retries for the same episode do not trigger duplicate downloads/processing.
4. **Reset and defer**: Manual reprocessing deletes the cache and resets the status, deferring work until the next client access.

---

## 2. Technical Design

### A. Database Schema Changes (`podcasts/models.py`)

1. **Episode Model**:
   * Change the `rehosted_media_id` field so that it is automatically generated upon model instance creation:
     ```python
     rehosted_media_id = models.UUIDField(
         default=uuid.uuid4,
         null=True, # Keep nullable for compatibility with existing records
         blank=True,
         help_text="ID of the rehosted media in the rehost_app database.",
     )
     ```
   * Update `rehosted_audio_url` to always generate the rehosted URL using `self.rehosted_media_id`, even if the episode status is not `COMPLETE` yet:
     ```python
     @property
     def rehosted_audio_url(self):
         if not self.rehosted_media_id:
             return None
         from django.conf import settings
         from django.urls import reverse
         relative_url = reverse(
             "serve_media_episode", kwargs={"media_guid": self.rehosted_media_id}
         )
         return f"{settings.REHOST_BASE_URL}{relative_url}"
     ```

### B. Feed Polling Changes (`podcasts/tasks.py`)

1. **Polling Task (`FeedManager.poll`)**:
   * Skip `self._dispatch_rehosting_tasks(episodes_to_process)`. 
   * This ensures that polling only registers episodes in the database (status `NEW`) without triggering immediate downloads or worker tasks.

### C. Rehosted Feed Changes (`podcasts/views.py`)

1. **Podcast RSS Feed View (`PodcastRSSFeedView`)**:
   * Modify the query to render **all** episodes for the podcast:
     ```python
     episodes = podcast.episodes.all().order_by("-pub_date")
     ```
     This matches the original feed's structure immediately, containing all episodes with valid rehosting URLs.

### D. Serve Rehosted Media View & Concurrency (`podcasts/views.py`)

1. **Lock Registry**:
   * Setup a global dictionary registry for episode-specific locks:
     ```python
     import threading
     import time
     
     media_locks = {}
     media_locks_lock = threading.Lock()
     
     def get_media_lock(media_guid):
         with media_locks_lock:
             if media_guid not in media_locks:
                 media_locks[media_guid] = threading.Lock()
             return media_locks[media_guid]
     ```

2. **Serve View Logic (`serve_rehosted_media`)**:
   * Attempt to find the `RehostedMedia` record.
     * **Cache Hit**: Serve the file.
     * **Cache Miss**:
       1. Look up the `Episode` where `rehosted_media_id=media_guid`. If not found, raise a `Http404`.
       2. Acquire the episode-specific lock using `get_media_lock(media_guid)`.
       3. Re-query/refresh the `Episode` from the database to check its status:
          * If `status == Episode.Status.COMPLETE`: Release the lock and serve the newly generated file.
          * If `status in [Episode.Status.DOWNLOADING, Episode.Status.ANALYZING, Episode.Status.PROCESSING]`:
            * Release the lock.
            * Sleep for 2 seconds.
            * Re-acquire the lock and repeat this check (polling the database status until it transitions to `COMPLETE` or `FAILED`).
          * If `status in [Episode.Status.NEW, Episode.Status.FAILED]`:
            * Set `status = Episode.Status.DOWNLOADING`.
            * Save the episode status.
            * **Release the lock** so that other concurrent request threads or retries can see the updated status and enter the sleep-and-poll loop instead of triggering duplicate downloads.
            * Run the processing synchronously:
              ```python
              from podcasts.tasks import EpisodeProcessor
              processor = EpisodeProcessor(episode)
              processor.rehost_audio()
              ```
            * Re-query the `RehostedMedia` record. If successful, serve the file. If processing failed (status `FAILED`), return a `Http404` or HTTP 500 error.

### E. Manual Actions Changes (`podcasts/views.py`)

1. **Reprocessing views (`EpisodeReprocessView` & `PodcastReprocessView`)**:
   * Instead of spawning background threads, update the logic to:
     * Delete the `RehostedMedia` database record and any physical file at `media_item.file_path`.
     * Reset the episode status to `Episode.Status.NEW`.
     * Clear the metadata fields (`rehosted_audio_size = 0`, `ad_segments = None`).
     * Note: We keep the existing `rehosted_media_id` UUID so the subscriber's RSS feed permalinks remain unchanged.
     * Do **not** spawn background processing tasks. Let the next access trigger the request on-demand.

---

## 3. Testing Strategy

1. **Unit Tests**:
   * Test the view `serve_rehosted_media` under different statuses (`COMPLETE`, `NEW`, `DOWNLOADING`).
   * Mock `EpisodeProcessor.rehost_audio` to verify that accessing a `NEW` status episode triggers synchronous execution.
   * Verify that calling the reprocess endpoints resets the status to `NEW` and deletes files/database records without starting threads.
2. **Concurrency Integration Tests**:
   * Test that multiple concurrent calls to `serve_rehosted_media` for a `NEW` status episode only invoke `rehost_audio` once, with the second request blocking/polling until the first one completes.
