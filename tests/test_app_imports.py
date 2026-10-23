import unittest


class TestAppImports(unittest.TestCase):
    def test_controller_imports(self):
        from doggybag.app.controller import AppController

        self.assertTrue(callable(AppController))

    def test_webview_host_imports(self):
        from doggybag.ui.webview_app import Bridge, WebViewPresenter, run

        self.assertTrue(callable(run))
        self.assertTrue(callable(Bridge))
        self.assertTrue(callable(WebViewPresenter))


if __name__ == "__main__":
    unittest.main()
