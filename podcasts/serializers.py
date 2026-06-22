"""
Serializers for the podcasts app.
"""
from rest_framework import serializers

from podcasts.models import Episode, Podcast


class EpisodeSerializer(serializers.ModelSerializer):
    """Serializer for the Episode model."""

    id = serializers.CharField(read_only=True)

    class Meta:
        model = Episode
        fields = "__all__"


class PodcastSerializer(serializers.ModelSerializer):
    """Serializer for the Podcast model."""

    id = serializers.CharField(read_only=True)
    episodes = EpisodeSerializer(many=True, read_only=True)
    download_all = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = Podcast
        fields = "__all__"
        read_only_fields = ["id", "title", "slug", "artwork_url", "episodes"]

    def validate_rss_url(self, value):
        """Check that the podcast is not already in the database."""
        if Podcast.objects.filter(rss_url=value).exists():
            raise serializers.ValidationError(
                "Podcast with this RSS URL already exists."
            )
        return value