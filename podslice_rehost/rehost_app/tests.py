from django.test import TestCase, RequestFactory
from django.http import Http404
from django.core.management import call_command
from django.conf import settings
from unittest.mock import patch, MagicMock
import uuid
import os
import json
import shutil
from pathlib import Path

from rehost_app.models import RehostedMedia, RehostedPodcast, RehostedEpisode
from rehost_app.views import serve_rehosted_media


class RehostedMediaModelTest(TestCase):
    def test_rehosted_media_creation(self):
        media_guid = uuid.uuid4()
        file_path = "/path/to/test/audio.mp3"
        content_type = "audio/mpeg"

        rehosted_media = RehostedMedia.objects.create(
            media_guid=media_guid,
            file_path=file_path,
            content_type=content_type
        )

        self.assertIsInstance(rehosted_media, RehostedMedia)
        self.assertEqual(rehosted_media.media_guid, media_guid)
        self.assertEqual(rehosted_media.file_path, file_path)
        self.assertEqual(rehosted_media.content_type, content_type)
        self.assertIsNotNone(rehosted_media.created_at)

    def test_rehosted_media_str_representation(self):
        media_guid = uuid.uuid4()
        rehosted_media = RehostedMedia.objects.create(
            media_guid=media_guid,
            file_path="/path/to/another/audio.mp3",
            content_type="audio/mpeg"
        )
        self.assertEqual(str(rehosted_media), f"Media {media_guid}")


class RehostedPodcastModelTest(TestCase):
    def test_podcast_creation(self):
        podcast = RehostedPodcast.objects.create(
            slug="test-podcast",
            original_rss_url="http://example.com/rss",
            title="Test Podcast",
            author="Test Author",
            artwork_url="http://example.com/artwork.jpg",
            description="A test podcast."
        )
        self.assertIsInstance(podcast, RehostedPodcast)
        self.assertEqual(podcast.slug, "test-podcast")
        self.assertEqual(podcast.title, "Test Podcast")

    def test_podcast_str_representation(self):
        podcast = RehostedPodcast.objects.create(
            slug="another-podcast",
            original_rss_url="http://example.com/another-rss",
            title="Another Podcast"
        )
        self.assertEqual(str(podcast), "Another Podcast")


class RehostedEpisodeModelTest(TestCase):
    def setUp(self):
        self.podcast = RehostedPodcast.objects.create(
            slug="test-podcast",
            original_rss_url="http://example.com/rss",
            title="Test Podcast"
        )

    def test_episode_creation(self):
        episode = RehostedEpisode.objects.create(
            podcast=self.podcast,
            guid="episode-guid-123",
            title="Test Episode",
            pub_date_iso="2025-01-01T12:00:00Z",
            description="A test episode.",
            absolute_file_path="/media/episodes/test.mp3",
            duration_seconds=3600,
            audio_file_size_bytes=1024000
        )
        self.assertIsInstance(episode, RehostedEpisode)
        self.assertEqual(episode.guid, "episode-guid-123")
        self.assertEqual(episode.title, "Test Episode")
        self.assertEqual(episode.podcast, self.podcast)

    def test_episode_str_representation(self):
        episode = RehostedEpisode.objects.create(
            podcast=self.podcast,
            guid="episode-guid-456",
            title="Another Episode",
            absolute_file_path="/media/episodes/another.mp3"
        )
        self.assertEqual(str(episode), "Test Podcast - Another Episode")

    def test_unique_together_constraint(self):
        RehostedEpisode.objects.create(
            podcast=self.podcast,
            guid="duplicate-guid",
            title="First Episode",
            absolute_file_path="/media/episodes/first.mp3"
        )
        with self.assertRaises(Exception) as cm:
            RehostedEpisode.objects.create(
                podcast=self.podcast,
                guid="duplicate-guid",
                title="Second Episode",
                absolute_file_path="/media/episodes/second.mp3"
            )
        self.assertIn("UNIQUE constraint failed", str(cm.exception))


class ServeRehostedMediaViewTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.media_guid = uuid.uuid4()
        self.test_file_path = os.path.join(settings.BASE_DIR, f"{self.media_guid}.mp3")
        with open(self.test_file_path, "wb") as f:
            f.write(b"This is a test audio file.")

        self.rehosted_media = RehostedMedia.objects.create(
            media_guid=self.media_guid,
            file_path=self.test_file_path,
            content_type="audio/mpeg"
        )

    def tearDown(self):
        if os.path.exists(self.test_file_path):
            os.remove(self.test_file_path)

    def test_serve_media_success(self):
        request = self.factory.get(f"/media/episodes/{self.media_guid}/")
        response = serve_rehosted_media(request, self.media_guid)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], "audio/mpeg")
        self.assertEqual(response.getvalue(), b"This is a test audio file.")

    def test_serve_media_not_found_in_db(self):
        non_existent_guid = uuid.uuid4()
        request = self.factory.get(f"/media/episodes/{non_existent_guid}/")
        with self.assertRaises(Http404) as cm:
            serve_rehosted_media(request, non_existent_guid)
        self.assertIn("Media file record not found in the database.", str(cm.exception))

    def test_serve_media_file_not_found_on_disk(self):
        # Delete the actual file but keep the DB record
        os.remove(self.test_file_path)
        request = self.factory.get(f"/media/episodes/{self.media_guid}/")
        with self.assertRaises(Http404) as cm:
            serve_rehosted_media(request, self.media_guid)
        self.assertIn("The media file is registered but was not found on disk.", str(cm.exception))


class RunConsumerCommandTest(TestCase):
    def setUp(self):
        self.message_dir = Path(settings.BASE_DIR) / "test_messages"
        self.failed_dir = self.message_dir / "failed"
        self.message_dir.mkdir(exist_ok=True)
        self.failed_dir.mkdir(exist_ok=True)

        # Patch MESSAGE_DIR in the command to point to our test directory
        self.patcher = patch('rehost_app.management.commands.run_consumer.MESSAGE_DIR', self.message_dir)
        self.mock_message_dir = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if self.message_dir.exists():
            shutil.rmtree(self.message_dir)

    def _create_message_file(self, filename, data):
        file_path = self.message_dir / filename
        with open(file_path, 'w') as f:
            json.dump(data, f)
        return file_path

    @patch('time.sleep', return_value=None) # Prevent actual sleeping during tests
    def test_consumer_processes_new_file(self, mock_sleep):
        podcast_data = {
            "slug": "test-podcast-slug",
            "original_rss_url": "http://example.com/rss",
            "title": "Test Podcast",
            "author": "Test Author",
            "artwork_url": "http://example.com/artwork.jpg",
            "description": "A test podcast."
        }
        episode_data = {
            "guid": "test-episode-guid",
            "title": "Test Episode",
            "pub_date_iso": "2025-01-01T12:00:00Z",
            "description": "A test episode.",
            "absolute_file_path": "/media/episodes/test.mp3",
            "duration_seconds": 3600,
            "audio_file_size_bytes": 1024000
        }
        message_data = {"podcast": podcast_data, "episode": episode_data}
        file_path = self._create_message_file("test_message.json", message_data)

        # Call the command's handle method directly for testing
        command = call_command('run_consumer', stdout=MagicMock(), stderr=MagicMock())

        # The consumer runs in a loop, so we need to simulate one iteration
        # by calling process_message_file directly or by mocking the loop.
        # For simplicity, we'll call process_message_file directly here.
        from rehost_app.management.commands.run_consumer import Command
        cmd_instance = Command()
        cmd_instance.stdout = MagicMock()
        cmd_instance.stderr = MagicMock()
        cmd_instance.process_message_file(file_path)

        self.assertFalse(file_path.exists()) # File should be deleted after processing
        self.assertTrue(RehostedPodcast.objects.filter(slug="test-podcast-slug").exists())
        self.assertTrue(RehostedEpisode.objects.filter(guid="test-episode-guid").exists())

    @patch('time.sleep', return_value=None)
    def test_consumer_handles_invalid_json(self, mock_sleep):
        file_path = self.message_dir / "invalid.json"
        with open(file_path, 'w') as f:
            f.write("{\"invalid_json")

        from rehost_app.management.commands.run_consumer import Command
        cmd_instance = Command()
        cmd_instance.stdout = MagicMock()
        cmd_instance.stderr = MagicMock()
        cmd_instance.process_message_file(file_path)

        self.assertFalse(file_path.exists()) # Original file should be gone
        self.assertTrue((self.failed_dir / "invalid.json").exists()) # Should be moved to failed
        cmd_instance.stderr.write.assert_called_with(cmd_instance.style.ERROR(f"Could not decode JSON from invalid.json. Moving to 'failed' folder."))

    @patch('time.sleep', return_value=None)
    def test_consumer_handles_missing_keys(self, mock_sleep):
        message_data = {"podcast": {"slug": "test"}}
        file_path = self._create_message_file("missing_keys.json", message_data)

        from rehost_app.management.commands.run_consumer import Command
        cmd_instance = Command()
        cmd_instance.stdout = MagicMock()
        cmd_instance.stderr = MagicMock()
        cmd_instance.process_message_file(file_path)

        self.assertFalse(file_path.exists())
        self.assertTrue((self.failed_dir / "missing_keys.json").exists())
        cmd_instance.stderr.write.assert_called_with(cmd_instance.style.ERROR(f"An error occurred with missing_keys.json: Message is missing 'podcast' or 'episode' key.. Moving to 'failed' folder."))

    @patch('time.sleep', return_value=None)
    def test_consumer_handles_duplicate_episode(self, mock_sleep):
        podcast_data = {"slug": "dup-podcast", "original_rss_url": "http://example.com/dup-rss", "title": "Dup Podcast"}
        episode_data = {"guid": "dup-episode", "title": "Dup Episode", "absolute_file_path": "/media/episodes/dup.mp3"}
        message_data = {"podcast": podcast_data, "episode": episode_data}

        file_path1 = self._create_message_file("dup1.json", message_data)
        from rehost_app.management.commands.run_consumer import Command
        cmd_instance = Command()
        cmd_instance.stdout = MagicMock()
        cmd_instance.stderr = MagicMock()
        cmd_instance.process_message_file(file_path1)

        self.assertFalse(file_path1.exists())
        self.assertTrue(RehostedEpisode.objects.filter(guid="dup-episode").exists())

        # Create a second file with the same data
        file_path2 = self._create_message_file("dup2.json", message_data)
        cmd_instance.process_message_file(file_path2)

        self.assertFalse(file_path2.exists())
        # Ensure only one episode record exists
        self.assertEqual(RehostedEpisode.objects.filter(guid="dup-episode").count(), 1)
        cmd_instance.stdout.write.assert_any_call(f"  -> Episode with GUID 'dup-episode' already exists. Skipped.")

    @patch('time.sleep', return_value=None)
    def test_move_to_failed(self, mock_sleep):
        file_path = self.message_dir / "test_file.json"
        file_path.touch()

        from rehost_app.management.commands.run_consumer import Command
        cmd_instance = Command()
        cmd_instance.move_to_failed(file_path)

        self.assertFalse(file_path.exists())
        self.assertTrue((self.failed_dir / "test_file.json").exists())


# Import the Command class from run_consumer.py for testing
from rehost_app.management.commands.run_consumer import Command