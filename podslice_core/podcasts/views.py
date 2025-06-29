import logging
import feedparser
from django.db import transaction
from rest_framework import generics, serializers
from celery.exceptions import CeleryError

from .models import Podcast, Episode
from .serializers import PodcastSerializer
from .tasks import poll_feed

logger = logging.getLogger(__name__)

BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404
from django.conf import settings
from datetime import datetime

class PodcastSubscriptionAPIView(generics.ListCreateAPIView):
    """
    API view to list existing podcast subscriptions and create new ones.
    Handles POST /api/podcasts/
    """
    queryset = Podcast.objects.all()
    serializer_class = PodcastSerializer

    def perform_create(self, serializer):
        """
        This method is called by DRF after validation and before saving the object.
        The entire operation is wrapped in a transaction to ensure atomicity.
        If any step fails, the database save is rolled back.
        """
        rss_url = serializer.validated_data['rss_url']
        logger.info(f"Attempting to subscribe to new feed: {rss_url}")

        try:
            # 2. Start an atomic transaction block.
            with transaction.atomic():
                # --- Step A: Parse the remote feed ---
                feed = feedparser.parse(rss_url, agent=BROWSER_USER_AGENT)
                if feed.bozo:
                    raise ValueError(f"Feed is malformed. Reason: {feed.get('bozo_exception', 'Unknown')}")
                if not feed.feed.get('title'):
                    raise ValueError("Could not find a title in the parsed feed.")

                # --- Step B: Save the Podcast object to the database ---
                title = feed.feed.get('title')
                artwork_url = feed.feed.get('image', {}).get('href')
                # The 'instance' is created but not yet permanently committed
                instance = serializer.save(title=title, artwork_url=artwork_url)
                logger.info(f"Successfully created Podcast ID {instance.id} for '{title}' (within transaction)")

                # --- Step C: Dispatch the background task ---
                poll_feed.delay(instance.id)
                logger.info(f"Successfully dispatched polling task for Podcast ID {instance.id}")

            # 3. If the code reaches here, the transaction is committed successfully.

        except (ValueError, IOError) as e:
            # Catches feed parsing or network errors. The transaction is automatically rolled back.
            logger.error(f"Failed to process RSS feed at {rss_url}.", exc_info=True)
            raise serializers.ValidationError({"non_field_errors": [f"Could not fetch or parse the feed. Reason: {e}"]})

        except CeleryError as e:
            # Catches errors from Celery (e.g., broker misconfigured). The transaction is automatically rolled back.
            logger.error(f"Celery task dispatch failed for podcast at {rss_url}.", exc_info=True)
            raise serializers.ValidationError({"non_field_errors": ["The system could not queue the podcast for processing. Please check the server configuration."]})

        except Exception as e:
            # A general catch-all. The transaction is automatically rolled back.
            logger.error(f"An unexpected error occurred during podcast subscription for {rss_url}.", exc_info=True)
            raise serializers.ValidationError({"non_field_errors": ["An unexpected server error occurred."]})


class PodcastRSSFeedView(generics.RetrieveAPIView):
    """
    API view to serve the re-hosted RSS feed for a podcast.
    Handles GET /feeds/podcasts/<uuid:podcast_id>/rss.xml
    """
    queryset = Podcast.objects.all()
    lookup_field = 'id'
    lookup_url_kwarg = 'podcast_id'

    def retrieve(self, request, *args, **kwargs):
        podcast = self.get_object()
        episodes = podcast.episodes.filter(status=Episode.Status.COMPLETE).order_by('-pub_date')

        context = {
            'podcast': podcast,
            'episodes': episodes,
            'build_date': datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z"),
            'rehost_base_url': settings.REHOST_BASE_URL,
        }
        rss_feed = render_to_string('podcasts/rss_feed_template.xml', context)
        return HttpResponse(rss_feed, content_type='application/xml')


class PodcastStatusAPIView(generics.RetrieveAPIView):
    """
    API view to retrieve the status of a podcast and its episodes.
    Handles GET /api/podcasts/<uuid:podcast_id>/status/
    """
    queryset = Podcast.objects.all()
    serializer_class = PodcastSerializer
    lookup_field = 'id'
    lookup_url_kwarg = 'podcast_id'