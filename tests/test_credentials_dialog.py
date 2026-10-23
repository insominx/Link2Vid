import unittest

from link2vid.app.controller import site_label as _site_label


class TestCredentialsDialog(unittest.TestCase):
    def test_site_label_strips_www(self):
        self.assertEqual(_site_label("https://www.example.com/path"), "example.com")

    def test_site_label_returns_host(self):
        self.assertEqual(_site_label("https://community.example.org/c/test"), "community.example.org")

    def test_site_label_empty_for_missing_url(self):
        self.assertIsNone(_site_label(None))
        self.assertIsNone(_site_label(""))


if __name__ == "__main__":
    unittest.main()
