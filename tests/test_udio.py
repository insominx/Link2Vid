import unittest
from unittest.mock import patch

from link2vid.core.fetcher import FetchError, FetchResults, VideoFetcher
from link2vid.core.udio import extract_udio_entries, is_udio_url, udio_song_id_from_url


SAMPLE_SONG = {
    "id": "ba4c1e6b-d014-4d24-8513-2db1e64cd1ed",
    "artist": "Xanderlin",
    "title": "Joyful Teamwork",
    "song_path": "https://storage.googleapis.com/udio-artifacts/sample.mp3",
    "image_path": "https://imagedelivery.net/example/cover/public",
    "duration": 32.8,
}


class TestUdioExtractor(unittest.TestCase):
    def test_is_udio_url_accepts_song_and_creator_hosts(self):
        self.assertTrue(is_udio_url("https://www.udio.com/songs/p1h7wicWYgm3jexNfumB2F"))
        self.assertTrue(is_udio_url("https://udio.com/creators/Xanderlin"))
        self.assertFalse(is_udio_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))

    def test_song_id_from_public_song_url(self):
        self.assertEqual(
            udio_song_id_from_url("https://www.udio.com/songs/p1h7wicWYgm3jexNfumB2F?foo=1"),
            "p1h7wicWYgm3jexNfumB2F",
        )
        self.assertIsNone(udio_song_id_from_url("https://www.udio.com/creators/Xanderlin"))

    def test_extract_udio_entries_builds_direct_mp3_card(self):
        entries = extract_udio_entries(
            "https://www.udio.com/songs/p1h7wicWYgm3jexNfumB2F",
            fetch_songs=lambda _ids: [SAMPLE_SONG],
        )
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["title"], "Xanderlin - Joyful Teamwork")
        self.assertEqual(entry["webpage_url"], SAMPLE_SONG["song_path"])
        self.assertEqual(entry["formats"][0]["ext"], "mp3")
        self.assertEqual(entry["thumbnail"], SAMPLE_SONG["image_path"])
        self.assertEqual(entry["duration"], 32.8)
        self.assertNotIn("_ffmpeg_headers", entry)

    def test_extract_udio_entries_skips_songs_without_audio(self):
        entries = extract_udio_entries(
            "https://www.udio.com/songs/p1h7wicWYgm3jexNfumB2F",
            fetch_songs=lambda _ids: [{"title": "Private", "song_path": None}],
        )
        self.assertEqual(entries, [])

    def test_extract_udio_entries_ignores_non_song_udio_urls(self):
        entries = extract_udio_entries(
            "https://www.udio.com/creators/Xanderlin",
            fetch_songs=lambda _ids: [SAMPLE_SONG],
        )
        self.assertEqual(entries, [])


class TestFetcherUdioRouting(unittest.TestCase):
    def test_fetch_returns_udio_entries_without_ytdlp(self):
        def fail_ytdlp(*_args, **_kwargs):
            raise AssertionError("yt-dlp must not run for public Udio song URLs")

        fetcher = VideoFetcher(get_video_info=fail_ytdlp)
        with patch(
            "link2vid.core.fetcher.extract_udio_entries",
            return_value=[{"title": "Xanderlin - Joyful Teamwork"}],
        ):
            outcome = fetcher.fetch("https://www.udio.com/songs/p1h7wicWYgm3jexNfumB2F")
        self.assertIsInstance(outcome, FetchResults)
        self.assertEqual(outcome.entries[0]["title"], "Xanderlin - Joyful Teamwork")

    def test_udio_without_public_audio_is_fetch_error_not_selenium(self):
        def fail_ytdlp(*_args, **_kwargs):
            raise AssertionError("yt-dlp must not run for Udio URLs")

        fetcher = VideoFetcher(get_video_info=fail_ytdlp)
        with patch("link2vid.core.fetcher.extract_udio_entries", return_value=[]):
            outcome = fetcher.fetch("https://www.udio.com/songs/missing")
        self.assertIsInstance(outcome, FetchError)
        self.assertIn("email link", str(outcome.error).lower())


if __name__ == "__main__":
    unittest.main()
