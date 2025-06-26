import uuid
from django.db import models

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