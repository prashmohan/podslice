import logging
import feedparser
import threading
import os
import json
from django.db import transaction
from rest_framework import generics, serializers
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.urls import reverse
from django.http import HttpResponse, FileResponse, Http404
from django.template.loader import render_to_string
from django.conf import settings
from datetime import datetime, timedelta

from .models import Podcast, Episode, RehostedMedia
from .serializers import PodcastSerializer
from .tasks import poll_feed, delete_podcast_data, reprocess_podcast

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

        for episode in episodes:
            episode.removed_duration_str = "N/A"
            if episode.ad_segments:
                try:
                    # The ad_segments can be a string from Gemini that needs to be cleaned
                    # or a direct JSON string stored in the model.
                    # This logic attempts to handle both cases.
                    cleaned_json = episode.ad_segments
                    if '```json' in cleaned_json:
                        cleaned_json = cleaned_json.split('```json\n')[1].split('\n```')[0]
                    
                    ad_segments_list = json.loads(cleaned_json)
                    
                    total_removed_seconds = sum(seg.get('end', 0) - seg.get('start', 0) for seg in ad_segments_list)
                    
                    if total_removed_seconds > 0:
                        duration = timedelta(seconds=total_removed_seconds)
                        total_minutes, remainder_seconds = divmod(int(duration.total_seconds()), 60)
                        episode.removed_duration_str = f"{total_minutes}m {remainder_seconds}s"
                    else:
                        episode.removed_duration_str = "0s"
                except (json.JSONDecodeError, TypeError, KeyError, IndexError) as e:
                    logger.warning(f"Could not parse ad_segments for episode {episode.id}. Error: {e}")
                    episode.removed_duration_str = "Error"

        return render(request, 'podcasts/status.html', {'podcast': podcast, 'episodes': episodes})

class PodcastRefreshView(View):
    def post(self, request, podcast_id):
        podcast = get_object_or_404(Podcast, id=podcast_id)
        poll_thread = threading.Thread(target=poll_feed, args=(podcast.id,))
        poll_thread.start()
        return redirect(reverse('podcast-status-ui', kwargs={'podcast_id': podcast.id}))


class PodcastReprocessView(View):
    def post(self, request, podcast_id):
        podcast = get_object_or_404(Podcast, id=podcast_id)
        reprocess_thread = threading.Thread(target=reprocess_podcast, args=(podcast.id,))
        reprocess_thread.start()
        return redirect(reverse('podcast-status-ui', kwargs={'podcast_id': podcast.id}))


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

class PodcastDeleteAPIView(generics.DestroyAPIView):
    """
    API view to delete a podcast subscription and its associated data.
    Handles DELETE /api/podcasts/<uuid:podcast_id>/
    """
    queryset = Podcast.objects.all()
    lookup_field = 'id'
    lookup_url_kwarg = 'podcast_id'

    def perform_destroy(self, instance):
        podcast_id = instance.id
        # Dispatch the deletion task to run in the background
        deletion_thread = threading.Thread(target=delete_podcast_data, args=(podcast_id,))
        deletion_thread.start()
        logger.info(f"Dispatched deletion task for Podcast ID: {podcast_id}")

def serve_rehosted_media(request, media_guid):
    """
    Serves a re-hosted audio file.

    This view is the heart of the podslice service. Its only job
    is to look up a media GUID in its database, find the corresponding
    physical file path on disk, and stream the file back to the client.

    Args:
        request: The Django HttpRequest object.
        media_guid: The UUID of the media file to be served.

    Returns:
        A FileResponse object that streams the media file, or raises
        an Http404 exception if the media is not found.
    """
    logger.info(f"Attempting to serve media for GUID: {media_guid}")
    try:
        media_item = RehostedMedia.objects.get(pk=media_guid)
        logger.info(f"Found RehostedMedia record for GUID {media_guid}. File path: {media_item.file_path}")
    except RehostedMedia.DoesNotExist:
        logger.error(f"RehostedMedia record not found in database for GUID: {media_guid}")
        raise Http404("Media file record not found in the database.")

    # Step 2: Perform a crucial security and integrity check.
    # Verify that the file path stored in the database actually points
    # to a file that exists on the server's filesystem.
    if not os.path.exists(media_item.file_path):
         # This could happen if a file was deleted manually.
         # Log this error in a real production system.
         logger.error(f"Media file not found on disk for GUID {media_guid}. Path: {media_item.file_path}")
         raise Http404("The media file is registered but was not found on disk.")

    # Step 3: Use Django's FileResponse.
    # This is the most efficient way to serve large files, as it streams
    # the file from disk directly to the response without loading the
    # entire file into memory. This is critical for performance and
    # for handling large podcast episodes.
    logger.info(f"Serving file {media_item.file_path} for GUID {media_guid}")
    response = FileResponse(
        open(media_item.file_path, 'rb'),
        content_type=media_item.content_type
    )
    return response
