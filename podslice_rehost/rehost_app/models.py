from django.db import models

class RehostedPodcast(models.Model):
    """
    Stores the metadata for a podcast that this service re-hosts.
    Data comes from messages published by the Core service.
    """
    original_rss_url = models.URLField(unique=True)
    slug = models.SlugField(max_length=255, unique=True)
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=255, blank=True, null=True)
    artwork_url = models.URLField(max_length=1024, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class RehostedEpisode(models.Model):
    """
    Stores the metadata for a single processed episode.
    """
    podcast = models.ForeignKey(RehostedPodcast, on_delete=models.CASCADE, related_name="episodes")
    guid = models.CharField(max_length=512, unique=True, help_text="Unique episode ID from source RSS.")
    title = models.CharField(max_length=255)
    pub_date_iso = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    absolute_file_path = models.CharField(max_length=1024)
    duration_seconds = models.PositiveIntegerField(blank=True, null=True)
    audio_file_size_bytes = models.PositiveBigIntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.podcast.title} - {self.title}"