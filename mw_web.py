"""MainWindow, part: FaceSort's browser and the gallery watcher (1.56)."""
import time

from PySide6.QtWidgets import QMessageBox


class WebMixin:
    def _web_ok(self):
        import inbrowser
        if inbrowser.available():
            return True
        QMessageBox.information(self, "FaceSort", "The browser part of FaceSort isn't installed (PySide6-WebEngine). "
                                                  "Run Setup.bat again, or install the latest version.")
        return False

    def web_window_open(self, url=None):
        """(1.57.1) The one Gallery Watcher window: browser on the left, watched galleries on the right."""
        if not self._web_ok():
            return None
        import inbrowser
        ww = getattr(self, "web_window", None)
        if ww is None:
            ww = self.web_window = inbrowser.BrowserWindow(self, url)
        elif url:
            ww.browse(url)
        ww.show()
        ww.raise_()
        ww.activateWindow()
        return ww

    def open_browser(self, url=None):
        return self.web_window_open(url)

    def open_gallery_watcher(self, check=False):
        ww = self.web_window_open()
        if ww is not None and check:
            ww.panel.check(None)
        return ww

    def check_galleries(self):
        """The toolbar button: check every watched gallery now."""
        import watcher
        if not self._web_ok():
            return
        if not watcher.watches(self.conn):
            if QMessageBox.question(self, "Check galleries", "You're not watching any galleries yet. Open the "
                                    "browser to find one (Watch this gallery), or add an address in the Gallery "
                                    "watcher?\n\nYes = browser, No = Gallery watcher") == QMessageBox.Yes:
                self.open_browser()
            else:
                self.open_gallery_watcher()
            return
        self.open_gallery_watcher(check=True)

    def import_gallery_files(self, paths, rule, name=""):
        """New pictures from a watched gallery: the Add photos window, filled in as set for the gallery
        (1.57.6 - you check the people and folders before the faces are looked for)."""
        return self.handle_drop(paths, defaults={"people": list(rule.get("people") or []),
                                                 "category": rule.get("category") or None,
                                                 "later": bool(rule.get("later"))},
                                source=f"New from the watched gallery “{name}”" if name else
                                "New from a watched gallery")

    def gallery_timer_tick(self):
        """Every few minutes: start a check by itself when one is due (Gallery watcher: Check every)."""
        import watcher
        try:
            ww = getattr(self, "web_window", None)
            if ww is not None and ww.panel.checker is not None:
                return
            if self.busy() or time.time() - getattr(self, "_gallery_last_try", 0) < 600:
                return
            if watcher.due(self.settings, self.conn):
                import inbrowser
                if inbrowser.available():
                    self._gallery_last_try = time.time()
                    self.open_gallery_watcher(check=True)
        except Exception:
            pass
