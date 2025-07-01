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
    rehosted_audio_size = models.BigIntegerField(default=0, help_text="Size of the rehosted audio file in bytes.")
    rehosted_media_id = models.UUIDField(null=True, blank=True, help_text="ID of the rehosted media in the rehost_app database.")
    ad_segments = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.podcast.title} - {self.title}"

class RehostedMedia(models.Model):
    """
    Represents a single piece of re-hosted media.
    This model provides a simple mapping from a public-facing UUID
    to the physical path of the audio file on disk.
    """
    media_guid = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="The unique identifier used in the public URL."
    )
    file_path = models.CharField(
        max_length=1024,
        help_text="The absolute path to the processed audio file on the shared media volume."
    )
    content_type = models.CharField(
        max_length=100,
        default='audio/mpeg',
        help_text="The MIME type of the audio file."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Media {self.media_guid}"

class RehostedPodcast(models.Model):
    """
    Represents a re-hosted podcast, storing metadata about the original podcast.
    """
    slug = models.CharField(
        max_length=255,
        unique=True,
        help_text="A unique slug for the podcast, derived from its original RSS feed URL."
    )
    original_rss_url = models.URLField(
        max_length=1024,
        help_text="The original RSS feed URL of the podcast."
    )
    title = models.CharField(
        max_length=500,
        help_text="The title of the podcast."
    )
    author = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="The author of the podcast."
    )
    artwork_url = models.URLField(
        max_length=1024,
        blank=True,
        null=True,
        help_text="URL to the podcast's artwork."
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Description of the podcast."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

class RehostedEpisode(models.Model):
    """
    Represents a re-hosted episode, linking to its podcast and storing episode-specific metadata.
    """
    podcast = models.ForeignKey(
        RehostedPodcast,
        on_delete=models.CASCADE,
        related_name='episodes',
        help_text="The podcast this episode belongs to."
    )
    guid = models.CharField(
        max_length=255,
        unique=True,
        help_text="The unique identifier (GUID) of the episode from the original RSS feed."
    )
    title = models.CharField(
        max_length=500,
        help_text="The title of the episode."
    )
    pub_date_iso = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Publication date of the episode in ISO format."
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Description of the episode."
    )
    absolute_file_path = models.CharField(
        max_length=1024,
        help_text="The absolute path to the processed audio file on the shared media volume."
    )
    duration_seconds = models.IntegerField(
        blank=True,
        null=True,
        help_text="Duration of the episode in seconds."
    )
    audio_file_size_bytes = models.BigIntegerField(
        blank=True,
        null=True,
        help_text="Size of the audio file in bytes."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('podcast', 'guid',) # Ensure GUIDs are unique per podcast

    def __str__(self):
        return f"{self.podcast.title} - {self.title}"