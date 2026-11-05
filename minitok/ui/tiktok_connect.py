"""'Continue with TikTok': runs login, the data request and the fit in the background."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from .. import tiktok_api
from ..algorithm import learner

DEV_PORTAL = "https://developers.tiktok.com/apps"
REDIRECT_TO_REGISTER = "http://127.0.0.1:*/callback/"


class TikTokSync(QObject):
    """Owned by the main window so it keeps going while you watch."""

    status = Signal(str)
    connected = Signal(str)  # display name
    learned = Signal(object, object)  # ExportData, LearnResult
    failed = Signal(str)

    def __init__(self, downloader, events, parent: QObject | None = None):
        super().__init__(parent)
        self.downloader = downloader
        self.events = events
        self.state = tiktok_api.ConnectState.load()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_status = self._idle_text()

    def _idle_text(self) -> str:
        s = self.state
        if s.pending_request_id:
            return "TikTok is preparing your data - MiniTok will finish by itself."
        if s.token and s.last_sync:
            who = f" as {s.display_name}" if s.display_name else ""
            return f"Connected{who}. Your algorithm was last updated from TikTok data."
        if s.token:
            return f"Connected{' as ' + s.display_name if s.display_name else ''}."
        return ""

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _emit_status(self, text: str) -> None:
        self.last_status = text
        self.status.emit(text)

    def start(self, login: bool = True) -> None:
        """Connect (if needed), fetch the data, fit and hand the result to the window."""
        if self.busy:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(login,), daemon=True)
        self._thread.start()

    def resume_if_pending(self) -> None:
        if self.state.pending_request_id and self.state.token:
            self.start(login=False)

    def stop(self) -> None:
        self._stop.set()

    def _run(self, login: bool) -> None:
        st = self.state
        try:
            if login and (st.token is None or st.pending_request_id is None):
                self._emit_status("Waiting for you to approve in your browser…")
                tiktok_api.login(st, should_stop=self._stop.is_set)
                try:
                    name = tiktok_api.display_name(st)
                except tiktok_api.TikTokError:
                    name = ""
                self.connected.emit(name)
            path = tiktok_api.sync(st, progress=self._emit_status, should_stop=self._stop.is_set)
            if path is None:
                return  # stopped (app closing); the request is resumed on next start
            self._emit_status("Learning your algorithm from your TikTok history…")
            export, result = learner.learn_from_export_file(
                path, fetch=self.downloader.metadata, sample=60, history=self.events,
                should_stop=self._stop.is_set,
                progress=lambda i, n, _v: self._emit_status(f"Learning your algorithm… ({i}/{n} videos checked)"),
            )
            self.last_status = "Connected. Your algorithm was just updated from your TikTok data."
            self.learned.emit(export, result)
        except tiktok_api.TikTokError as exc:
            self.last_status = str(exc)
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything else instead of dying silently
            self.last_status = f"Something went wrong: {exc}"
            self.failed.emit(self.last_status)


class DeveloperAppDialog(QDialog):
    """One-time setup for whoever runs this copy of MiniTok: link their TikTok developer app."""

    def __init__(self, state: tiktok_api.ConnectState, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Link your TikTok developer app")
        self.state = state
        lay = QVBoxLayout(self)
        intro = QLabel(
            "TikTok only shares data with registered apps. This is a one-time step for the owner of this "
            "copy of MiniTok; after it, connecting is a single tap.<br><br>"
            f"1. On <a href='{DEV_PORTAL}'>developers.tiktok.com</a>, create an app and add "
            "<b>Login Kit</b> and the <b>Data Portability API</b> (scope "
            "<code>portability.activity.single</code>). TikTok reviews it, usually in 3-4 weeks.<br>"
            f"2. Under Login Kit › Desktop, register the redirect URI <code>{REDIRECT_TO_REGISTER}</code>.<br>"
            "3. Paste the app's <b>Client key</b> and <b>Client secret</b> below."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        intro.setOpenExternalLinks(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.key = QLineEdit(state.client_key)
        self.secret = QLineEdit(state.client_secret)
        self.secret.setEchoMode(QLineEdit.Password)
        self.scopes = QLineEdit(state.scopes or tiktok_api.DEFAULT_SCOPES)
        form.addRow("Client key", self.key)
        form.addRow("Client secret", self.secret)
        form.addRow("Scopes", self.scopes)
        lay.addLayout(form)
        note = QLabel("Stored only on this computer, in tiktok.json in the MiniTok data folder.")
        note.setObjectName("Hint")
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("Primary")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(520, 380)

    def _save(self) -> None:
        if not self.key.text().strip() or not self.secret.text().strip():
            self.key.setFocus()
            return
        self.state.client_key = self.key.text().strip()
        self.state.client_secret = self.secret.text().strip()
        self.state.scopes = self.scopes.text().strip() or tiktok_api.DEFAULT_SCOPES
        self.state.save()
        self.accept()
