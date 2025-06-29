import logging
import feedparser
import threading
from django.db import transaction
from rest_framework import generics, serializers
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.urls import reverse
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.conf import settings
from datetime import datetime

from .models import Podcast, Episode
from .serializers import PodcastSerializer
from .tasks import poll_feed

logger = logging.getLogger(__name__)

BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

def create_podcast_from_url(rss_url: str) -> Podcast:
    """
    Parses an RSS feed, creates a Podcast object, and dispatches a background task.
    """
    logger.info(f"Starting subscription process for RSS URL: {rss_url}")
    try:
        with transaction.atomic():
            logger.debug(f"Parsing feed: {rss_url}")
            feed = feedparser.parse(rss_url, agent=BROWSER_USER_AGENT)
            if feed.bozo:
                logger.error(f"Malformed feed at {rss_url}. Reason: {feed.get('bozo_exception', 'Unknown')}")
                raise ValueError(f"Feed is malformed. Reason: {feed.get('bozo_exception', 'Unknown')}")
            
            feed_title = feed.feed.get('title')
            if not feed_title:
                logger.error(f"No title found in feed: {rss_url}")
                raise ValueError("Could not find a title in the parsed feed.")
            
            logger.debug(f"Feed '{feed_title}' parsed successfully.")

            artwork_url = feed.feed.get('image', {}).get('href')
            
            podcast, created = Podcast.objects.get_or_create(
                rss_url=rss_url,
                defaults={'title': feed_title, 'artwork_url': artwork_url}
            )

            if not created:
                logger.warning(f"Subscription attempt for existing feed: {rss_url}. Podcast ID: {podcast.id}")
                return podcast

            logger.info(f"New podcast '{feed_title}' created with ID: {podcast.id}. Dispatching polling task.")
            poll_thread = threading.Thread(target=poll_feed, args=(podcast.id,))
            poll_thread.start()
            logger.debug(f"Polling task for Podcast ID {podcast.id} dispatched successfully.")
        
        return podcast
    except (ValueError, IOError) as e:
        logger.error(f"Failed to process RSS feed at {rss_url}.", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred during podcast subscription for {rss_url}.", exc_info=True)
        raise

class PodcastSubscribeUIView(View):
    """
    A view to render the subscription form and handle its submission.
    """
    def get(self, request):
        podcasts = Podcast.objects.all()
        return render(request, 'podcasts/subscribe.html', {'podcasts': podcasts})

    def post(self, request):
        rss_url = request.POST.get('rss_url')
        if not rss_url:
            podcasts = Podcast.objects.all()
            return render(request, 'podcasts/subscribe.html', {'error': 'RSS URL is required.', 'podcasts': podcasts})

        try:
            podcast = create_podcast_from_url(rss_url)
            return redirect(reverse('podcast-status-ui', kwargs={'podcast_id': podcast.id}))
        except Exception as e:
            podcasts = Podcast.objects.all()
            return render(request, 'podcasts/subscribe.html', {'error': str(e), 'rss_url': rss_url, 'podcasts': podcasts})

class PodcastStatusUIView(View):
    """
    A view to display the status of a podcast subscription.
    """
    def get(self, request, podcast_id):
        podcast = get_object_or_404(Podcast, id=podcast_id)
        episodes = podcast.episodes.all().order_by('-pub_date')
        return render(request, 'podcasts/status.html', {'podcast': podcast, 'episodes': episodes})

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
        """
        rss_url = serializer.validated_data['rss_url']
        try:
            create_podcast_from_url(rss_url)
        except (ValueError, IOError) as e:
            raise serializers.ValidationError({"rss_url": [f"Could not fetch or parse the feed. Reason: {e}"]})
        except Exception as e:
            raise serializers.ValidationError({"non_field_errors": [f"An unexpected server error occurred: {e}"]})


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