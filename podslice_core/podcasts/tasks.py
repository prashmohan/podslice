from celery import shared_task

@shared_task
def poll_feed(podcast_id):
    """
    A placeholder task to poll a podcast feed for new episodes.
    """
    print(f"Task dispatched: Polling feed for Podcast ID: {podcast_id}")
    # Full logic for this task will be implemented in Task #3.
    return f"Polling started for podcast {podcast_id}"
    