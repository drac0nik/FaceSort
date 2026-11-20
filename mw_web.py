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

    def open_browser(self, url=None):
        if not self._web_ok():
            return None
        import inbrowser
        b = inbrowser.BrowserWindow(self, url)
        self._browsers = [x for x in getattr(self, "_browsers", []) if x.isVisible()] + [b]
        b.show()
        return b

    def open_gallery_watcher(self, check=False):
        if not self._web_ok():
            return None
        import watcher_ui
        if getattr(self, "watcher_window", None) is None:
            self.watcher_window = watcher_ui.WatcherWindow(self)
        w = self.watcher_window
        w.show()
        w.raise_()
        w.activateWindow()
        if check:
            w.check(None)
        return w

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

    def import_gallery_files(self, paths, rule):
        """New pictures from a watched gallery: into Recently dropped, tagged as set for it."""
        if rule.get("ask"):
            return self.handle_drop(paths)
        return self.handle_drop(paths, answers={"people": list(rule.get("people") or []),
                                                "category": rule.get("category") or None,
                                                "later": bool(rule.get("later"))})

    def gallery_timer_tick(self):
        """Every few minutes: start a check by itself when one is due (Gallery watcher: Check every)."""
        import watcher
        try:
            if getattr(self, "watcher_window", None) is not None and self.watcher_window.checker is not None:
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
