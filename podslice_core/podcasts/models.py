import uuid
from django.db import models
from django.utils.text import slugify
from django.utils import timezone

class Podcast(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
        ANALYZING = 'ANALYZING', 'Analyzing'
        PROCESSING = 'PROCESSING', 'Processing'
        COMPLETE = 'COMPLETE', 'Complete'
        FAILED = 'FAILED', 'Failed'

    podcast = models.ForeignKey(Podcast, on_delete=models.CASCADE, related_name='episodes')
    guid = models.CharField(max_length=512, unique=True, help_text="Unique episode ID from RSS feed.")
    title = models.CharField(max_length=255)
    pub_date = models.DateTimeField()
    original_audio_url = models.URLField(max_length=1024)
    rehosted_audio_url = models.URLField(null=True, blank=True)
    ad_segments = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.podcast.title} - {self.title}"