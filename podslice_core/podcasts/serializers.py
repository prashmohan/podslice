from rest_framework import serializers
from .models import Podcast

class PodcastSerializer(serializers.ModelSerializer):
    class Meta:
        model = Podcast
        # The fields to include in the API response
        fields = ['id', 'title', 'slug', 'rss_url', 'artwork_url']
        # The user only provides the rss_url. The other fields are populated
        # by our view logic after parsing the feed, so they are read-only.
        read_only_fields = ['id', 'title', 'slug', 'artwork_url']

    def validate_rss_url(self, value):
        """
        Check that the podcast is not already in the database.
        """
        if Podcast.objects.filter(rss_url=value).exists():
            raise serializers.ValidationError("Podcast with this Rss url already exists.")
        return value
        