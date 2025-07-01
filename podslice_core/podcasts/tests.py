from django.test import TestCase
from django.utils import timezone
from datetime import datetime
import uuid
from unittest import mock
import time
import requests # Import requests
import io

from rest_framework.test import APIClient
from rest_framework import status
from django.urls import reverse

from .models import Podcast, Episode, RehostedMedia
from .serializers import PodcastSerializer, EpisodeSerializer
from .tasks import poll_feed, rehost_episode_audio
from .views import create_podcast_from_url
from pydub import AudioSegment

class PodcastModelTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml",
            artwork_url="http://example.com/artwork.jpg"
        )

    def test_podcast_creation(self):
        self.assertIsInstance(self.podcast, Podcast)
        self.assertEqual(self.podcast.title, "Test Podcast")
        self.assertEqual(self.podcast.rss_url, "http://example.com/feed.xml")
        self.assertEqual(self.podcast.artwork_url, "http://example.com/artwork.jpg")

    def test_podcast_str_representation(self):
        self.assertEqual(str(self.podcast), "Test Podcast")

class EpisodeModelTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml",
            artwork_url="http://example.com/artwork.jpg"
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid=str(uuid.uuid4()),
            pub_date=timezone.make_aware(datetime(2024, 1, 1)),
            original_audio_url="http://example.com/audio.mp3",
            status=Episode.Status.NEW
        )

    def test_episode_creation(self):
        self.assertIsInstance(self.episode, Episode)
        self.assertEqual(self.episode.podcast, self.podcast)
        self.assertEqual(self.episode.title, "Test Episode")
        self.assertIsNotNone(self.episode.guid)
        self.assertEqual(self.episode.pub_date, timezone.make_aware(datetime(2024, 1, 1)))
        self.assertEqual(self.episode.original_audio_url, "http://example.com/audio.mp3")
        self.assertEqual(self.episode.status, Episode.Status.NEW)
        self.assertIsNone(self.episode.rehosted_audio_url)
        self.assertIsNone(self.episode.rehosted_media_id)
        self.assertIsNone(self.episode.ad_segments)
        self.assertIsNotNone(self.episode.created_at)
        self.assertIsNotNone(self.episode.updated_at)

    def test_episode_str_representation(self):
        self.assertEqual(str(self.episode), "Test Podcast - Test Episode")

    def test_episode_status_choices(self):
        for status_choice in Episode.Status:
            episode = Episode.objects.create(
                podcast=self.podcast,
                title=f"Episode with status {status_choice.value}",
                guid=str(uuid.uuid4()),
                pub_date=timezone.now(),
                original_audio_url="http://example.com/audio.mp3",
                status=status_choice.value
            )
            self.assertEqual(episode.status, status_choice.value)


class PodcastSerializerTest(TestCase):
    def setUp(self):
        self.podcast_data = {
            'rss_url': 'http://example.com/new_feed.xml'
        }
        self.podcast = Podcast.objects.create(
            title="Existing Podcast",
            rss_url="http://example.com/existing_feed.xml",
            artwork_url="http://example.com/artwork.jpg"
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Existing Episode",
            guid=str(uuid.uuid4()),
            pub_date=timezone.now(),
            original_audio_url="http://example.com/existing_audio.mp3",
            status=Episode.Status.COMPLETE,
            rehosted_audio_url="http://rehost.example.com/audio/123/",
            rehosted_media_id=uuid.uuid4(),
            ad_segments='[]'
        )

    def test_podcast_serializer_valid_data(self):
        serializer = PodcastSerializer(data=self.podcast_data)
        self.assertTrue(serializer.is_valid())

    def test_podcast_serializer_duplicate_rss_url(self):
        duplicate_data = {'rss_url': 'http://example.com/existing_feed.xml'}
        serializer = PodcastSerializer(data=duplicate_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('rss_url', serializer.errors)
        self.assertEqual(str(serializer.errors['rss_url'][0]), "podcast with this rss url already exists.")

    def test_podcast_serializer_representation(self):
        serializer = PodcastSerializer(instance=self.podcast)
        data = serializer.data
        self.assertEqual(data['id'], str(self.podcast.id))
        self.assertEqual(data['title'], self.podcast.title)
        self.assertEqual(data['rss_url'], self.podcast.rss_url)
        self.assertEqual(data['artwork_url'], self.podcast.artwork_url)
        
        self.assertIn('episodes', data)
        self.assertEqual(len(data['episodes']), 1)
        self.assertEqual(data['episodes'][0]['id'], str(self.episode.id))


class EpisodeSerializerTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml",
            artwork_url="http://example.com/artwork.jpg"
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid=str(uuid.uuid4()),
            pub_date=timezone.now(),
            original_audio_url="http://example.com/audio.mp3",
            status=Episode.Status.COMPLETE,
            rehosted_audio_url="http://rehost.example.com/audio/abc/",
            ad_segments='[{"start": 10, "end": 20}]'
        )

    def test_episode_serializer_representation(self):
        serializer = EpisodeSerializer(instance=self.episode)
        data = serializer.data
        self.assertEqual(data['id'], str(self.episode.id))
        self.assertEqual(data['title'], self.episode.title)
        
        self.assertEqual(data['original_audio_url'], self.episode.original_audio_url)
        self.assertEqual(data['rehosted_audio_url'], self.episode.rehosted_audio_url)


class PodcastViewsTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml",
            artwork_url="http://example.com/artwork.jpg"
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid=str(uuid.uuid4()),
            pub_date=timezone.now(),
            original_audio_url="http://example.com/audio.mp3",
            status=Episode.Status.COMPLETE,
            rehosted_audio_url="http://rehost.example.com/audio/abc/",
            ad_segments='[]'
        )

    @mock.patch('podcasts.views.create_podcast_from_url')
    def test_podcast_subscription_api_view_post_success(self, mock_create_podcast):
        data = {'rss_url': 'http://example.com/new_podcast_feed.xml'}
        response = self.client.post(reverse('podcast-subscribe-api'), data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_create_podcast.assert_called_once_with('http://example.com/new_podcast_feed.xml')

    @mock.patch('podcasts.views.create_podcast_from_url')
    def test_podcast_subscription_api_view_post_invalid_feed(self, mock_create_podcast):
        mock_create_podcast.side_effect = ValueError("Invalid Feed")
        data = {'rss_url': 'http://example.com/invalid_feed.xml'}
        response = self.client.post(reverse('podcast-subscribe-api'), data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Could not fetch or parse the feed', str(response.data))

    def test_podcast_subscription_api_view_get(self):
        response = self.client.get(reverse('podcast-subscribe-api'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['title'], self.podcast.title)

    def test_podcast_rss_feed_view(self):
        response = self.client.get(reverse('podcast-rss-feed-api', kwargs={'podcast_id': self.podcast.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/xml')
        self.assertIn(f'<title>{self.podcast.title} (Ad-Free)</title>', response.content.decode())
        self.assertIn(f'<enclosure url="{self.episode.rehosted_audio_url}"', response.content.decode())

    def test_podcast_rss_feed_view_not_found(self):
        non_existent_uuid = uuid.uuid4()
        url = reverse('podcast-rss-feed-api', kwargs={'podcast_id': non_existent_uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_podcast_status_api_view(self):
        url = reverse('podcast-status-api', kwargs={'podcast_id': self.podcast.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], str(self.podcast.id))
        self.assertEqual(response.data['title'], self.podcast.title)
        self.assertIn('episodes', response.data)
        self.assertEqual(len(response.data['episodes']), 1)
        self.assertEqual(response.data['episodes'][0]['id'], str(self.episode.id))

    def test_podcast_status_api_view_not_found(self):
        non_existent_uuid = uuid.uuid4()
        response = self.client.get(reverse('podcast-status-api', kwargs={'podcast_id': non_existent_uuid}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @mock.patch('podcasts.views.delete_podcast_data')
    def test_podcast_delete_api_view(self, mock_delete_podcast_data):
        # Create a podcast to delete
        podcast_to_delete = Podcast.objects.create(
            title="Podcast to Delete",
            rss_url="http://example.com/delete_feed.xml",
            artwork_url="http://example.com/delete_artwork.jpg"
        )
        # Create an episode for the podcast
        Episode.objects.create(
            podcast=podcast_to_delete,
            title="Episode to Delete",
            guid=str(uuid.uuid4()),
            pub_date=timezone.now(),
            original_audio_url="http://example.com/delete_audio.mp3",
            status=Episode.Status.COMPLETE,
            rehosted_audio_url="http://rehost.example.com/audio/delete/",
            rehosted_media_id=uuid.uuid4(),
            ad_segments='[]'
        )

        url = reverse('podcast-delete-api', kwargs={'podcast_id': podcast_to_delete.id})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_delete_podcast_data.assert_called_once_with(podcast_to_delete.id)
        # Verify that the podcast still exists in the DB immediately after the API call
        # because deletion is dispatched to a background task.
        self.assertTrue(Podcast.objects.filter(id=podcast_to_delete.id).exists())

    def test_podcast_delete_api_view_not_found(self):
        non_existent_uuid = uuid.uuid4()
        url = reverse('podcast-delete-api', kwargs={'podcast_id': non_existent_uuid})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

class PodcastTasksTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml"
        )

    def _create_mock_feed(self, entries):
        return mock.Mock(
            bozo=0,
            entries=entries
        )

    def _create_mock_entry(self, guid, title, audio_url, pub_date_tuple):
        entry_data = {
            "id": guid,
            "guid": guid,
            "title": title,
            "published_parsed": time.struct_time(pub_date_tuple) if pub_date_tuple else None,
        }
        enclosures = []
        if audio_url:
            enclosures.append({"type": "audio/mpeg", "href": audio_url})

        mock_entry = mock.Mock(**entry_data)
        mock_entry.get.side_effect = lambda key, default=None: {
            "id": guid,
            "guid": guid,
            "title": title,
            "published_parsed": time.struct_time(pub_date_tuple) if pub_date_tuple else None,
            "enclosures": enclosures
        }.get(key, default)
        return mock_entry

    @mock.patch('podcasts.tasks.threading.Thread')
    @mock.patch('podcasts.tasks.feedparser.parse')
    def test_poll_feed_idempotency(self, mock_feedparser_parse, mock_thread):
        entry1_guid = "guid1"
        entry1 = self._create_mock_entry(
            entry1_guid, "Episode 1", "http://example.com/ep1.mp3", (2024, 1, 1, 12, 0, 0, 1, 1, 0)
        )
        mock_feed = self._create_mock_feed([entry1])
        mock_feedparser_parse.return_value = mock_feed

        poll_feed(self.podcast.id)
        self.assertEqual(Episode.objects.count(), 1)
        mock_thread.assert_called_once()

        poll_feed(self.podcast.id)
        self.assertEqual(Episode.objects.count(), 1)
        mock_thread.assert_called_once()

    @mock.patch('podcasts.tasks.feedparser.parse')
    def test_poll_feed_malformed_feed(self, mock_feedparser_parse):
        mock_feedparser_parse.return_value = mock.Mock(bozo=1, bozo_exception="It's broken")
        
        poll_feed(self.podcast.id)
        
        self.assertEqual(Episode.objects.count(), 0)

    @mock.patch('podcasts.tasks.feedparser.parse')
    def test_poll_feed_podcast_not_found(self, mock_feedparser_parse):
        non_existent_uuid = uuid.uuid4()
        poll_feed(non_existent_uuid)

    @mock.patch('podcasts.tasks.feedparser.parse')
    def test_poll_feed_entry_missing_guid(self, mock_feedparser_parse):
        entry_no_guid = self._create_mock_entry(None, "No GUID", "http://example.com/ep.mp3", (2024, 1, 1, 12, 0, 0, 1, 1, 0))
        mock_feed = self._create_mock_feed([entry_no_guid])
        mock_feedparser_parse.return_value = mock_feed

        poll_feed(self.podcast.id)
        self.assertEqual(Episode.objects.count(), 0)

    @mock.patch('podcasts.tasks.feedparser.parse')
    def test_poll_feed_entry_missing_audio(self, mock_feedparser_parse):
        entry_no_audio = self._create_mock_entry("guid1", "No Audio", None, (2024, 1, 1, 12, 0, 0, 1, 1, 0))
        mock_feed = self._create_mock_feed([entry_no_audio])
        mock_feedparser_parse.return_value = mock_feed

        poll_feed(self.podcast.id)
        self.assertEqual(Episode.objects.count(), 0)

    @mock.patch('podcasts.tasks.requests.get')
    @mock.patch('podcasts.tasks.genai.GenerativeModel')
    @mock.patch('podcasts.tasks.AudioSegment.from_file')
    @mock.patch('podcasts.tasks.os.makedirs')
    @mock.patch('podcasts.tasks.os.remove')
    def test_rehost_episode_audio_success(self, mock_os_remove, mock_os_makedirs, mock_audio_segment_from_file, mock_generative_model, mock_requests_get):
        mock_response = mock.Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_content.return_value = [b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"]
        mock_requests_get.return_value = mock_response

        mock_gemini_model_instance = mock.Mock()
        mock_gemini_model_instance.generate_content.return_value = mock.Mock(text='[{"start": 10, "end": 20}]')
        mock_generative_model.return_value = mock_gemini_model_instance

        # Create a real AudioSegment object for testing
        real_audio_segment = AudioSegment.silent(duration=100000)
        mock_audio_segment_from_file.return_value = real_audio_segment

        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode for Rehost",
            guid=str(uuid.uuid4()),
            pub_date=timezone.now(),
            original_audio_url="http://example.com/audio.mp3",
            status=Episode.Status.NEW
        )

        result = rehost_episode_audio(episode.id)

        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.COMPLETE)
        self.assertIsNotNone(episode.rehosted_audio_url)
        mock_requests_get.assert_called_once_with(episode.original_audio_url, stream=True)
        mock_generative_model.assert_called_once_with('gemini-2.5-flash')
        mock_audio_segment_from_file.assert_called_once()
        mock_os_remove.assert_called_once()

    @mock.patch('podcasts.tasks.requests.get')
    def test_rehost_episode_audio_download_failure(self, mock_requests_get):
        mock_requests_get.side_effect = requests.exceptions.RequestException("Download failed")

        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode for Rehost",
            guid=str(uuid.uuid4()),
            pub_date=timezone.now(),
            original_audio_url="http://example.com/audio.mp3",
            status=Episode.Status.NEW
        )

        rehost_episode_audio(episode.id)

        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.FAILED)

    @mock.patch('podcasts.tasks.requests.get')
    @mock.patch('podcasts.tasks.genai.GenerativeModel')
    @mock.patch('podcasts.tasks.os.remove')
    def test_rehost_episode_audio_gemini_failure(self, mock_os_remove, mock_generative_model, mock_requests_get):
        mock_response = mock.Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_content.return_value = [b"audio_data"]
        mock_requests_get.return_value = mock_response

        mock_generative_model.side_effect = Exception("Gemini error")

        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode for Rehost",
            guid=str(uuid.uuid4()),
            pub_date=timezone.now(),
            original_audio_url="http://example.com/audio.mp3",
            status=Episode.Status.NEW
        )

        rehost_episode_audio(episode.id)

        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.FAILED)
        mock_os_remove.assert_called_once()

    @mock.patch('podcasts.tasks.requests.get')
    @mock.patch('podcasts.tasks.genai.GenerativeModel')
    @mock.patch('podcasts.tasks.AudioSegment.from_file')
    @mock.patch('podcasts.tasks.os.remove')
    def test_rehost_episode_audio_pydub_failure(self, mock_os_remove, mock_audio_segment_from_file, mock_generative_model, mock_requests_get):
        mock_response = mock.Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_content.return_value = [b"audio_data"]
        mock_requests_get.return_value = mock_response

        mock_gemini_model_instance = mock.Mock()
        mock_gemini_model_instance.generate_content.return_value = mock.Mock(text='[{"start": 10, "end": 20}]')
        mock_generative_model.return_value = mock_gemini_model_instance

        mock_audio_segment_from_file.side_effect = Exception("Pydub error")

        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode for Rehost",
            guid=str(uuid.uuid4()),
            pub_date=timezone.now(),
            original_audio_url="http://example.com/audio.mp3",
            status=Episode.Status.NEW
        )

        rehost_episode_audio(episode.id)

        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.FAILED)
        mock_os_remove.assert_called_once()

    @mock.patch('podcasts.tasks.requests.get')
    @mock.patch('podcasts.tasks.genai.GenerativeModel')
    @mock.patch('podcasts.tasks.AudioSegment.from_file')
    @mock.patch('podcasts.tasks.os.makedirs')
    @mock.patch('podcasts.tasks.os.remove')
    @mock.patch('podcasts.tasks.RehostedMedia.objects.create')
    def test_rehost_episode_audio_rehosted_media_save_failure(self, mock_rehosted_media_create, mock_os_remove, mock_os_makedirs, mock_audio_segment_from_file, mock_generative_model, mock_requests_get):
        mock_response = mock.Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_content.return_value = [b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"]
        mock_requests_get.return_value = mock_response

        mock_gemini_model_instance = mock.Mock()
        mock_gemini_model_instance.generate_content.return_value = mock.Mock(text='[{"start": 10, "end": 20}]')
        mock_generative_model.return_value = mock_gemini_model_instance

        # Create a real AudioSegment object for testing
        real_audio_segment = AudioSegment.silent(duration=100000)
        mock_audio_segment_from_file.return_value = real_audio_segment

        mock_rehosted_media_create.side_effect = Exception("DB save error")

        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode for Rehost",
            guid=str(uuid.uuid4()),
            pub_date=timezone.now(),
            original_audio_url="http://example.com/audio.mp3",
            status=Episode.Status.NEW
        )

        rehost_episode_audio(episode.id)

        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.FAILED)
        mock_os_remove.assert_called_once()

    def test_rehost_episode_audio_episode_not_found(self):
        non_existent_uuid = uuid.uuid4()
        rehost_episode_audio(non_existent_uuid)

class PodcastUITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.podcast = Podcast.objects.create(
            title="UI Test Podcast",
            rss_url="http://example.com/ui_feed.xml",
        )

    def test_subscribe_ui_get(self):
        response = self.client.get(reverse('podcast-subscribe-ui'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Subscribe to a New Podcast")
        self.assertContains(response, self.podcast.title)

    @mock.patch('podcasts.views.create_podcast_from_url')
    def test_subscribe_ui_post_success(self, mock_create_podcast):
        new_podcast_id = uuid.uuid4()
        mock_create_podcast.return_value = Podcast(id=new_podcast_id, title="New Podcast")
        
        data = {'rss_url': 'http://example.com/new_ui_feed.xml'}
        response = self.client.post(reverse('podcast-subscribe-ui'), data)
        
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(response.url, reverse('podcast-status-ui', kwargs={'podcast_id': new_podcast_id}))
        mock_create_podcast.assert_called_once_with('http://example.com/new_ui_feed.xml')

    @mock.patch('podcasts.views.create_podcast_from_url')
    def test_subscribe_ui_post_failure(self, mock_create_podcast):
        mock_create_podcast.side_effect = Exception("Test Error")
        
        data = {'rss_url': 'http://example.com/bad_feed.xml'}
        response = self.client.post(reverse('podcast-subscribe-ui'), data)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Test Error")

    def test_status_ui_view(self):
        response = self.client.get(reverse('podcast-status-ui', kwargs={'podcast_id': self.podcast.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, self.podcast.title)

class CreatePodcastFromUrlTest(TestCase):
    @mock.patch('podcasts.views.threading.Thread')
    @mock.patch('podcasts.views.feedparser.parse')
    def test_create_podcast_from_url_success(self, mock_feedparser_parse, mock_thread):
        mock_feedparser_parse.return_value = mock.Mock(
            bozo=0,
            feed={'title': 'New Podcast', 'image': {'href': 'http://example.com/new_artwork.jpg'}}
        )
        
        podcast = create_podcast_from_url('http://example.com/new_podcast_feed.xml')
        
        self.assertIsInstance(podcast, Podcast)
        self.assertEqual(podcast.title, "New Podcast")
        self.assertEqual(Podcast.objects.count(), 1)
        mock_thread.assert_called_once_with(target=poll_feed, args=(podcast.id,))

    @mock.patch('podcasts.views.feedparser.parse')
    def test_create_podcast_from_url_already_exists(self, mock_feedparser_parse):
        Podcast.objects.create(rss_url="http://example.com/existing.xml", title="Existing")
        mock_feedparser_parse.return_value = mock.Mock(bozo=0, feed={'title': 'Does not matter'})

        podcast = create_podcast_from_url("http://example.com/existing.xml")
        
        self.assertEqual(Podcast.objects.count(), 1)
        self.assertEqual(podcast.title, "Existing")

class PollFeedEpisodeLimitTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml"
        )

    def _create_mock_feed(self, entries):
        return mock.Mock(
            bozo=0,
            entries=entries
        )

    def _create_mock_entry(self, guid, title, audio_url, pub_date_tuple):
        entry_data = {
            "id": guid,
            "guid": guid,
            "title": title,
            "published_parsed": time.struct_time(pub_date_tuple) if pub_date_tuple else None,
        }
        enclosures = []
        if audio_url:
            enclosures.append({"type": "audio/mpeg", "href": audio_url})

        mock_entry = mock.Mock(**entry_data)
        mock_entry.get.side_effect = lambda key, default=None: {
            "id": guid,
            "guid": guid,
            "title": title,
            "published_parsed": time.struct_time(pub_date_tuple) if pub_date_tuple else None,
            "enclosures": enclosures
        }.get(key, default)
        return mock_entry

    @mock.patch('podcasts.tasks.threading.Thread')
    @mock.patch('podcasts.tasks.feedparser.parse')
    def test_poll_feed_limits_episodes(self, mock_feedparser_parse, mock_thread):
        entry1 = self._create_mock_entry("guid1", "Episode 1", "http://example.com/ep1.mp3", (2024, 1, 3, 12, 0, 0, 1, 3, 0))
        entry2 = self._create_mock_entry("guid2", "Episode 2", "http://example.com/ep2.mp3", (2024, 1, 2, 12, 0, 0, 1, 2, 0))
        entry3 = self._create_mock_entry("guid3", "Episode 3", "http://example.com/ep3.mp3", (2024, 1, 1, 12, 0, 0, 1, 1, 0))
        
        mock_feed = self._create_mock_feed([entry1, entry2, entry3])
        mock_feedparser_parse.return_value = mock_feed

        poll_feed(self.podcast.id)

        self.assertEqual(Episode.objects.count(), 2)
        self.assertTrue(Episode.objects.filter(guid="guid1").exists())
        self.assertTrue(Episode.objects.filter(guid="guid2").exists())
        self.assertFalse(Episode.objects.filter(guid="guid3").exists())
        self.assertEqual(mock_thread.call_count, 2)

class RehostedMediaViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.media_guid = uuid.uuid4()
        self.file_path = "/tmp/test_audio.mp3"
        self.rehosted_media = RehostedMedia.objects.create(
            media_guid=self.media_guid,
            file_path=self.file_path,
            content_type="audio/mpeg"
        )
        with open(self.file_path, "wb") as f:
            f.write(b"test audio data")

    def tearDown(self):
        import os
        os.remove(self.file_path)

    def test_serve_rehosted_media_success(self):
        url = reverse('serve_media_episode', kwargs={'media_guid': self.media_guid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'audio/mpeg')

    def test_serve_rehosted_media_not_found(self):
        non_existent_uuid = uuid.uuid4()
        url = reverse('serve_media_episode', kwargs={'media_guid': non_existent_uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)