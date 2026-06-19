import unittest


class TestAppImports(unittest.TestCase):
    def test_main_window_imports(self):
        from link2vid.ui.main_window import VideoDownloaderApp

        self.assertTrue(callable(VideoDownloaderApp))


if __name__ == "__main__":
    unittest.main()
