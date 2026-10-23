import unittest
from unittest.mock import MagicMock, patch

from doggybag.core.fetcher import FetchError, FetchResults, NeedsLogin, VideoFetcher
from doggybag.core.gdcvault import (
    GdcVaultLoginError,
    GdcVaultLoginRequired,
    extract_gdcvault_entries,
    is_gdcvault_url,
)


TALK_URL = "https://gdcvault.com/play/1035781/Bringing-Narrative-to-Disney-Dreamlight"
PLAYER_ID = "5ff208f0041347baa01beeea3b2ca0ac"
PLAYBACK_URL = (
    f"https://cdn-a.blazestreaming.com/out/v1/{PLAYER_ID}"
    "/38549d9a185b445888c6fbc2e2e792e9/50413c5b8a95441e919f0a1579606e47/index.m3u8"
)
UNLOCKED_HTML = f"""
<html><head><meta property="og:title" content="Bringing Narrative to 'Disney Dreamlight Valley'" /></head>
<body><iframe src="https://gdcvault.blazestreaming.com/?id={PLAYER_ID}"></iframe></body></html>
"""
GATED_HTML = """
<html><head><meta property="og:title" content="Bringing Narrative to 'Disney Dreamlight Valley'" /></head>
<body><form id="form_login_page" method="POST" action="/api/login.php"></form></body></html>
"""
NO_PLAYER_HTML = "<html><head><title>GDC Vault - Old Talk</title></head><body></body></html>"


class FakeSession:
    """Serves the talk page gated until a successful login POST."""

    def __init__(self, *, login_status=200, login_text="", page_after_login=UNLOCKED_HTML, page_before=GATED_HTML):
        self.login_status = login_status
        self.login_text = login_text
        self.page_before = page_before
        self.page_after_login = page_after_login
        self.logged_in = False
        self.calls: list[tuple[str, str]] = []

    def get(self, url, **_kwargs):
        self.calls.append(("GET", url))
        if url.endswith("/aliases.json"):
            return MagicMock(status_code=404)
        if url.endswith("/logout"):
            self.logged_in = False
            return MagicMock(status_code=200)
        return MagicMock(text=self.page_after_login if self.logged_in else self.page_before)

    def post(self, url, data=None, **_kwargs):
        self.calls.append(("POST", url))
        self.posted = data
        if self.login_status == 200:
            self.logged_in = True
        return MagicMock(status_code=self.login_status, text=self.login_text)


class TestGdcVaultExtractor(unittest.TestCase):
    def test_is_gdcvault_url(self):
        self.assertTrue(is_gdcvault_url(TALK_URL))
        self.assertTrue(is_gdcvault_url("https://www.gdcvault.com/play/1"))
        self.assertFalse(is_gdcvault_url("https://notgdcvault.com/play/1"))

    def test_free_talk_returns_entry_without_login(self):
        session = FakeSession(page_before=UNLOCKED_HTML)
        entries = extract_gdcvault_entries(TALK_URL, session_factory=lambda: session)
        self.assertEqual([entry["webpage_url"] for entry in entries], [PLAYBACK_URL])
        self.assertEqual(entries[0]["title"], "Bringing Narrative to 'Disney Dreamlight Valley'")
        self.assertNotIn("POST", [method for method, _ in session.calls])

    def test_members_only_talk_without_credentials_requires_login(self):
        session = FakeSession()
        with self.assertRaises(GdcVaultLoginRequired):
            extract_gdcvault_entries(TALK_URL, session_factory=lambda: session)

    def test_members_only_talk_signs_in_resolves_and_signs_out(self):
        session = FakeSession()
        entries = extract_gdcvault_entries(
            TALK_URL, "member@example.com", "secret", session_factory=lambda: session
        )
        self.assertEqual([entry["webpage_url"] for entry in entries], [PLAYBACK_URL])
        self.assertEqual(session.posted, {"email": "member@example.com", "password": "secret"})
        self.assertIn(("POST", "https://gdcvault.com/api/login.php"), session.calls)
        self.assertEqual(session.calls[-1], ("GET", "https://gdcvault.com/logout"))
        self.assertNotIn("Cookie", entries[0]["_ffmpeg_headers"])

    def test_rejected_credentials_surface_site_message(self):
        session = FakeSession(login_status=500, login_text="Username or Password is Incorrect.")
        with self.assertRaisesRegex(GdcVaultLoginError, "Username or Password is Incorrect"):
            extract_gdcvault_entries(TALK_URL, "member@example.com", "wrong", session_factory=lambda: session)
        self.assertNotIn(("GET", "https://gdcvault.com/logout"), session.calls)

    def test_account_without_access_still_gated_signs_out_and_errors(self):
        session = FakeSession(page_after_login=GATED_HTML)
        with self.assertRaisesRegex(GdcVaultLoginError, "members-only access"):
            extract_gdcvault_entries(TALK_URL, "free@example.com", "secret", session_factory=lambda: session)
        self.assertEqual(session.calls[-1], ("GET", "https://gdcvault.com/logout"))

    def test_page_without_player_or_gate_returns_empty(self):
        session = FakeSession(page_before=NO_PLAYER_HTML)
        self.assertEqual(extract_gdcvault_entries(TALK_URL, session_factory=lambda: session), [])


class TestFetcherGdcVaultRouting(unittest.TestCase):
    def test_free_talk_skips_broken_ytdlp_extractor(self):
        def fail_ytdlp(*_args, **_kwargs):
            raise AssertionError("yt-dlp must not run when the Blazestreaming player resolves")

        fetcher = VideoFetcher(get_video_info=fail_ytdlp)
        with patch("doggybag.core.fetcher.extract_gdcvault_entries", return_value=[{"title": "Talk"}]):
            outcome = fetcher.fetch(TALK_URL)
        self.assertIsInstance(outcome, FetchResults)

    def test_members_only_without_credentials_asks_for_login_not_selenium(self):
        fetcher = VideoFetcher(get_video_info=MagicMock())
        with patch(
            "doggybag.core.fetcher.extract_gdcvault_entries",
            side_effect=GdcVaultLoginRequired("members-only"),
        ):
            outcome = fetcher.fetch(TALK_URL)
        self.assertIsInstance(outcome, NeedsLogin)

    def test_rejected_login_is_fetch_error(self):
        fetcher = VideoFetcher(get_video_info=MagicMock())
        with patch(
            "doggybag.core.fetcher.extract_gdcvault_entries",
            side_effect=GdcVaultLoginError("Username or Password is Incorrect."),
        ):
            outcome = fetcher.fetch(TALK_URL, "member@example.com", "wrong")
        self.assertIsInstance(outcome, FetchError)

    def test_older_talk_without_player_falls_back_to_ytdlp_with_credentials(self):
        get_video_info = MagicMock(return_value=[{"title": "Old talk"}])
        fetcher = VideoFetcher(get_video_info=get_video_info)
        with patch("doggybag.core.fetcher.extract_gdcvault_entries", return_value=[]):
            outcome = fetcher.fetch(TALK_URL, "member@example.com", "secret")
        self.assertIsInstance(outcome, FetchResults)
        get_video_info.assert_called_once_with(TALK_URL, "member@example.com", "secret")

    def test_older_talk_ytdlp_failure_is_fetch_error_not_selenium(self):
        fetcher = VideoFetcher(get_video_info=MagicMock(side_effect=IndexError("list index out of range")))
        with patch("doggybag.core.fetcher.extract_gdcvault_entries", return_value=[]):
            outcome = fetcher.fetch(TALK_URL)
        self.assertIsInstance(outcome, FetchError)
        self.assertIn("No playable GDC Vault video", str(outcome.error))


if __name__ == "__main__":
    unittest.main()
