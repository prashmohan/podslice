from django.db import models
from django.utils.text import slugify

class Podcast(models.Model):
    title = models.CharField(max_length=255, help_text="Title of the podcast, parsed from feed.")
    slug = models.SlugField(max_length=255, unique=True, help_text="URL-friendly slug, generated from title.")
    rss_url = models.URLField(unique=True, help_text="The unique URL of the podcast's RSS feed.")
    artwork_url = models.URLField(blank=True, null=True)
    # Note: Other metadata fields can be added here later if needed.

    def save(self, *args, **kwargs):
        # Automatically generate the slug from the title if it doesn't exist.
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.title

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
    # Stores the path *inside the container* or relative to MEDIA_ROOT
    original_audio_path = models.CharField(max_length=512, blank=True, null=True)
    processed_audio_path = models.CharField(max_length=512, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    failure_reason = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return f"{self.podcast.title} - {self.title}"