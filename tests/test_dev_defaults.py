import unittest

from link2vid.core.dev_defaults import (
    credential_entries,
    dev_credentials_for_url,
    dev_domain,
    dev_domain_for_url,
    host_matches_domain,
    resolve_login_plan,
    url_matches_dev_domain,
)
from tests.fixtures.hosts import AUTH_HOST_A, AUTH_HOST_B, AUTH_LOGIN_HOST, VIDEO_HOST_A


class TestDevDefaults(unittest.TestCase):
    def test_host_matches_domain(self):
        self.assertTrue(host_matches_domain(f"www.{AUTH_HOST_A}", AUTH_HOST_A))
        self.assertTrue(host_matches_domain(AUTH_HOST_B, AUTH_HOST_A))
        self.assertFalse(host_matches_domain(VIDEO_HOST_A, AUTH_HOST_A))

    def test_url_matches_dev_domain(self):
        dev = {"default_domain": AUTH_HOST_A}
        self.assertTrue(url_matches_dev_domain(f"https://www.{AUTH_HOST_A}/play/1", dev))
        self.assertFalse(url_matches_dev_domain(f"https://{VIDEO_HOST_A}/c/test", dev))

    def test_dev_credentials_require_domain_match(self):
        dev = {
            "use_defaults": True,
            "default_domain": AUTH_HOST_A,
            "default_username": "user@example.com",
            "default_password": "secret",
        }
        username, password = dev_credentials_for_url(f"https://www.{AUTH_HOST_A}/play/1", dev)
        self.assertEqual(username, "user@example.com")
        self.assertEqual(password, "secret")

        username, password = dev_credentials_for_url(f"https://{VIDEO_HOST_A}/c/test", dev)
        self.assertIsNone(username)
        self.assertIsNone(password)

    def test_dev_credentials_missing_domain_returns_none(self):
        dev = {
            "use_defaults": True,
            "default_username": "user@example.com",
            "default_password": "secret",
        }
        username, password = dev_credentials_for_url(f"https://www.{AUTH_HOST_A}/play/1", dev)
        self.assertIsNone(username)
        self.assertIsNone(password)

    def test_dev_domain_accepts_domain_alias(self):
        dev = {"domain": VIDEO_HOST_A}
        self.assertEqual(dev_domain(dev), VIDEO_HOST_A)

    def test_sites_array_selects_matching_credentials(self):
        dev = {
            "use_defaults": True,
            "sites": [
                {
                    "domain": AUTH_HOST_A,
                    "username": "gdc-user",
                    "password": "gdc-pass",
                },
                {
                    "domain": VIDEO_HOST_A,
                    "username": "aa-user",
                    "password": "aa-pass",
                },
            ],
        }
        username, password = dev_credentials_for_url(
            f"https://community.{VIDEO_HOST_A}/c/bootcamp-replays/test",
            dev,
        )
        self.assertEqual(username, "aa-user")
        self.assertEqual(password, "aa-pass")
        self.assertEqual(dev_domain_for_url(
            f"https://app.{VIDEO_HOST_A}/login",
            dev,
        ), VIDEO_HOST_A)

    def test_empty_site_credentials_fall_back_to_manual(self):
        dev = {
            "use_defaults": True,
            "sites": [{"domain": VIDEO_HOST_A, "username": "", "password": ""}],
        }
        username, password = dev_credentials_for_url(
            f"https://community.{VIDEO_HOST_A}/c/test",
            dev,
        )
        self.assertIsNone(username)
        self.assertIsNone(password)

    def test_legacy_flat_fields_still_work(self):
        dev = {
            "use_defaults": True,
            "default_domain": AUTH_HOST_A,
            "default_username": "legacy-user",
            "default_password": "legacy-pass",
        }
        self.assertEqual(len(credential_entries(dev)), 1)
        username, password = dev_credentials_for_url(f"https://www.{AUTH_HOST_A}/play/1", dev)
        self.assertEqual(username, "legacy-user")
        self.assertEqual(password, "legacy-pass")

    def test_resolve_login_plan_uses_matching_site_override(self):
        dev = {
            "sites": [
                {
                    "domain": AUTH_HOST_A,
                    "login_url": f"https://{AUTH_LOGIN_HOST}/signin",
                    "login_mode": "form",
                }
            ]
        }
        plan = resolve_login_plan(f"https://www.{AUTH_HOST_A}/play/1", dev)
        self.assertEqual(plan.login_url, f"https://{AUTH_LOGIN_HOST}/signin")
        self.assertEqual(plan.login_mode, "form")

    def test_resolve_login_plan_defaults_to_page_origin(self):
        plan = resolve_login_plan(f"https://{VIDEO_HOST_A}/watch/1", {})
        self.assertEqual(plan.login_url, f"https://{VIDEO_HOST_A}/login")
        self.assertEqual(plan.login_mode, "generic")


if __name__ == "__main__":
    unittest.main()
