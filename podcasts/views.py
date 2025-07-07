"""
Views for the podcasts app.
"""
import json
import logging
import os
import threading
from datetime import datetime, timedelta

import feedparser
from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View
from rest_framework import generics, serializers

import xml.etree.ElementTree as ET

from podcasts.forms import OPMLImportForm
from podcasts.models import Episode, Podcast, RehostedMedia
from podcasts.serializers import PodcastSerializer
from podcasts.tasks import delete_podcast_data, poll_feed, reprocess_podcast

logger = logging.getLogger(__name__)

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36"
)


class OPMLImportView(View):
    """
    A view to import podcast subscriptions from an OPML file.
    """

    def get(self, request):
        """
        Handles GET requests and returns an OPML import page.
        """
        form = OPMLImportForm()
        return render(request, "podcasts/opml_import.html", {"form": form})

    def post(self, request):
        """
        Handles POST requests and imports podcast subscriptions from an OPML file.
        """
        form = OPMLImportForm(request.POST, request.FILES)
        if form.is_valid():
            opml_file = request.FILES["opml_file"]
            try:
                tree = ET.parse(opml_file)
                root = tree.getroot()
                for outline in root.findall(".//outline[@type='rss']"):
                    rss_url = outline.get("xmlUrl")
                    if rss_url:
                        try:
                            create_podcast_from_url(rss_url)
                        except Exception as e:
                            logger.error(
                                "Error creating podcast from URL %s: %s", rss_url, e
                            )
                return redirect("podcast-subscribe-ui")
            except ET.ParseError as e:
                form.add_error("opml_file", f"Invalid OPML file: {e}")
        return render(request, "podcasts/opml_import.html", {"form": form})


class OPMLExportView(View):
    """
    A view to export all podcast subscriptions as an OPML file.
    """

    def get(self, request):
        """
        Handles GET requests and returns an OPML file.
        """
        podcasts = Podcast.objects.all()
        for podcast in podcasts:
            podcast.rehosted_rss_url = request.build_absolute_uri(
                reverse("podcast-rss-feed-api", args=[podcast.id])
            )

        context = {
            "podcasts": podcasts,
            "created_at": datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z"),
        }

        opml_content = render_to_string("podcasts/opml_export.xml", context)

        response = HttpResponse(opml_content, content_type="application/xml")
        response["Content-Disposition"] = (
            'attachment; filename="podslice_subscriptions.opml"'
        )
        return response


class OPMLBackupView(View):
    """
    A view to export all podcast subscriptions as an OPML file with the original RSS URLs.
    """

    def get(self, request):
        """
        Handles GET requests and returns an OPML file.
        """
        podcasts = Podcast.objects.all()

        context = {
            "podcasts": podcasts,
            "created_at": datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z"),
            "is_backup": True,
        }

        opml_content = render_to_string("podcasts/opml_export.xml", context)

        response = HttpResponse(opml_content, content_type="application/xml")
        response["Content-Disposition"] = (
            'attachment; filename="podslice_backup.opml"'
        )
        return response


def create_podcast_from_url(rss_url: str) -> Podcast:
    """
    Parses an RSS feed, creates a Podcast object, and dispatches a background task.
    """
    logger.info("Starting subscription process for RSS URL: %s", rss_url)
    try:
        with transaction.atomic():
            logger.debug("Parsing feed: %s", rss_url)
            feed = feedparser.parse(rss_url, agent=BROWSER_USER_AGENT)
            if feed.bozo:
                logger.error(
                    "Malformed feed at %s. Reason: %s",
                    rss_url,
                    feed.get("bozo_exception", "Unknown"),
                )
                raise ValueError(
                    f"Feed is malformed. Reason: {feed.get('bozo_exception', 'Unknown')}"
                )

            feed_title = feed.feed.get("title")
            if not feed_title:
                logger.error("No title found in feed: %s", rss_url)
                raise ValueError("Could not find a title in the parsed feed.")

            logger.debug("Feed '%s' parsed successfully.", feed_title)

            artwork_url = feed.feed.get("image", {}).get("href")

            podcast, created = Podcast.objects.get_or_create(
                rss_url=rss_url,
                defaults={"title": feed_title, "artwork_url": artwork_url},
            )

            if not created:
                logger.warning(
                    "Subscription attempt for existing feed: %s. Podcast ID: %s",
                    rss_url,
                    podcast.id,
                )
                return podcast

            logger.info(
                "New podcast '%s' created with ID: %s. Dispatching polling task.",
                feed_title,
                podcast.id,
            )
            poll_thread = threading.Thread(target=poll_feed, args=(podcast.id,))
            poll_thread.start()
            logger.debug(
                "Polling task for Podcast ID %s dispatched successfully.", podcast.id
            )

        return podcast
    except (ValueError, IOError):
        logger.error("Failed to process RSS feed at %s.", rss_url, exc_info=True)
        raise
    except Exception as e:
        logger.error(
            "An unexpected error occurred during podcast subscription for %s.",
            rss_url,
            exc_info=True,
        )
        raise e


class PodcastSubscribeUIView(View):
    """
    A view to render the subscription form and handle its submission.
    """

    def get(self, request):
        """
        Handles GET requests and renders the subscription form.
        """
        podcasts = Podcast.objects.all()
        return render(request, "podcasts/subscribe.html", {"podcasts": podcasts})

    def post(self, request):
        """
        Handles POST requests and creates a new podcast subscription.
        """
        rss_url = request.POST.get("rss_url")
        if not rss_url:
            podcasts = Podcast.objects.all()
            return render(
                request,
                "podcasts/subscribe.html",
                {"error": "RSS URL is required.", "podcasts": podcasts},
            )

        try:
            podcast = create_podcast_from_url(rss_url)
            return redirect(
                reverse("podcast-status-ui", kwargs={"podcast_id": podcast.id})
            )
        except Exception as e:
            podcasts = Podcast.objects.all()
            return render(
                request,
                "podcasts/subscribe.html",
                {"error": str(e), "rss_url": rss_url, "podcasts": podcasts},
            )


class PodcastStatusUIView(View):
    """
    A view to display the status of a podcast subscription.
    """

    def get(self, request, podcast_id):
        """
        Handles GET requests and renders the podcast status page.
        """
        podcast = get_object_or_404(Podcast, id=podcast_id)
        episodes = podcast.episodes.all().order_by("-pub_date")

        for episode in episodes:
            episode.removed_duration_str = "N/A"
            if episode.ad_segments:
                try:
                    cleaned_json = episode.ad_segments
                    if "```json" in cleaned_json:
                        cleaned_json = cleaned_json.split("```json\n")[1].split(
                            "\n```"
                        )[0]

                    ad_segments_list = json.loads(cleaned_json)

                    total_removed_seconds = sum(
                        seg.get("end", 0) - seg.get("start", 0)
                        for seg in ad_segments_list
                    )

                    if total_removed_seconds > 0:
                        duration = timedelta(seconds=total_removed_seconds)
                        total_minutes, remainder_seconds = divmod(
                            int(duration.total_seconds()), 60
                        )
                        episode.removed_duration_str = (
                            f"{total_minutes}m {remainder_seconds}s"
                        )
                    else:
                        episode.removed_duration_str = "0s"
                except (json.JSONDecodeError, TypeError, KeyError, IndexError) as e:
                    logger.warning(
                        "Could not parse ad_segments for episode %s. Error: %s",
                        episode.id,
                        e,
                    )
                    episode.removed_duration_str = "Error"

        return render(
            request, "podcasts/status.html", {"podcast": podcast, "episodes": episodes}
        )


class PodcastRefreshView(View):
    """
    A view to refresh a podcast feed.
    """

    def post(self, request, podcast_id):
        """
        Handles POST requests and refreshes a podcast feed.
        """
        podcast = get_object_or_404(Podcast, id=podcast_id)
        poll_thread = threading.Thread(target=poll_feed, args=(podcast.id,))
        poll_thread.start()
        return redirect(reverse("podcast-status-ui", kwargs={"podcast_id": podcast.id}))


class PodcastReprocessView(View):
    """
    A view to reprocess a podcast.
    """

    def post(self, request, podcast_id):
        """
        Handles POST requests and reprocesses a podcast.
        """
        podcast = get_object_or_404(Podcast, id=podcast_id)
        reprocess_thread = threading.Thread(
            target=reprocess_podcast, args=(podcast.id,)
        )
        reprocess_thread.start()
        return redirect(reverse("podcast-status-ui", kwargs={"podcast_id": podcast.id}))


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
        rss_url = serializer.validated_data["rss_url"]
        try:
            create_podcast_from_url(rss_url)
        except (ValueError, IOError) as e:
            raise serializers.ValidationError(
                {"rss_url": [f"Could not fetch or parse the feed. Reason: {e}"]}
            ) from e
        except Exception as e:
            raise serializers.ValidationError(
                {"non_field_errors": [f"An unexpected server error occurred: {e}"]}
            ) from e


class PodcastRSSFeedView(generics.RetrieveAPIView):
    """
    API view to serve the re-hosted RSS feed for a podcast.
    Handles GET /feeds/podcasts/<uuid:podcast_id>/rss.xml
    """

    queryset = Podcast.objects.all()
    lookup_field = "id"
    lookup_url_kwarg = "podcast_id"

    def retrieve(self, request, *args, **kwargs):
        """
        Handles GET requests and returns the re-hosted RSS feed.
        """
        podcast = self.get_object()
        episodes = podcast.episodes.filter(status=Episode.Status.COMPLETE).order_by(
            "-pub_date"
        )

        context = {
            "podcast": podcast,
            "episodes": episodes,
            "build_date": datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z"),
            "rehost_base_url": settings.REHOST_BASE_URL,
        }
        rss_feed = render_to_string("podcasts/rss_feed_template.xml", context)
        return HttpResponse(rss_feed, content_type="application/xml")


class PodcastStatusAPIView(generics.RetrieveAPIView):
    """
    API view to retrieve the status of a podcast and its episodes.
    Handles GET /api/podcasts/<uuid:podcast_id>/status/
    """

    queryset = Podcast.objects.all()
    serializer_class = PodcastSerializer
    lookup_field = "id"
    lookup_url_kwarg = "podcast_id"


class PodcastDeleteAPIView(generics.DestroyAPIView):
    """
    API view to delete a podcast subscription and its associated data.
    Handles DELETE /api/podcasts/<uuid:podcast_id>/
    """

    queryset = Podcast.objects.all()
    lookup_field = "id"
    lookup_url_kwarg = "podcast_id"

    def perform_destroy(self, instance):
        """
        Handles DELETE requests and deletes a podcast subscription.
        """
        podcast_id = instance.id
        # Dispatch the deletion task to run in the background
        deletion_thread = threading.Thread(
            target=delete_podcast_data, args=(podcast_id,)
        )
        deletion_thread.start()
        logger.info("Dispatched deletion task for Podcast ID: %s", podcast_id)


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
    logger.info("Attempting to serve media for GUID: %s", media_guid)
    try:
        media_item = RehostedMedia.objects.get(pk=media_guid)
        logger.info(
            "Found RehostedMedia record for GUID %s. File path: %s",
            media_guid,
            media_item.file_path,
        )
    except RehostedMedia.DoesNotExist as e:
        logger.error(
            "RehostedMedia record not found in database for GUID: %s", media_guid
        )
        raise Http404("Media file record not found in the database.") from e

    if not os.path.exists(media_item.file_path):
        logger.error(
            "Media file not found on disk for GUID %s. Path: %s",
            media_guid,
            media_item.file_path,
        )
        raise Http404("The media file is registered but was not found on disk.")

    logger.info("Serving file %s for GUID %s", media_item.file_path, media_guid)
    try:
        with open(media_item.file_path, "rb") as f:
            response = FileResponse(f, content_type=media_item.content_type)
    except FileNotFoundError as e:
        raise Http404("The media file is registered but was not found on disk.") from e
    return response


from django.http import JsonResponse
from django.views import View
from podcasts.models import Episode


class EpisodeStatusAPIView(View):
    """
    API view to retrieve the status of all episodes.
    """

    def get(self, request):
        """
        Handles GET requests and returns the number of completed and pending episodes.
        """
        completed_episodes = Episode.objects.filter(status=Episode.Status.COMPLETE).count()
        pending_episodes = Episode.objects.exclude(
            status__in=[Episode.Status.COMPLETE, Episode.Status.FAILED]
        ).count()

        data = {
            "completed": completed_episodes,
            "pending": pending_episodes,
        }
        return JsonResponse(data)
