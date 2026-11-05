"""The phone-sized For You window."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QFileDialog,
    QGraphicsDropShadowEffect,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import comments as comments_io
from ..algorithm.presets import PRESETS
from ..config import AppConfig, data_dir
from ..downloader import Downloader
from ..events import EventLog
from ..feed import Feed, FeedListener, WatchStats
from ..models import Comment, Video, tiktok_count
from ..sources import LocalFolderSource, Source, YtDlpSource
from ..storage import Cache
from .comments_sheet import CommentsSheet


def build_sources(config: AppConfig, downloader: Downloader) -> list[Source]:
    sources: list[Source] = []
    if config.local_folder:
        sources.append(LocalFolderSource(config.local_folder))
    for spec in config.sources:
        if spec.strip():
            sources.append(YtDlpSource(spec.strip(), downloader.ydl))
    return sources


class FeedSignals(QObject):
    videoReady = Signal(object, object)
    videoFailed = Signal(object, str)
    commentsReady = Signal(object, list)
    commentsFailed = Signal(object, str)
    status = Signal(str)
    candidatesReady = Signal()


class QtListener(FeedListener):
    """Feed callbacks arrive on worker threads; Qt queues the signals onto the UI thread."""

    def __init__(self, signals: FeedSignals):
        self.s = signals

    def video_ready(self, video: Video, path: Path) -> None:
        self.s.videoReady.emit(video, path)

    def video_failed(self, video: Video, error: str) -> None:
        self.s.videoFailed.emit(video, error)

    def comments_ready(self, video: Video, comments: list[Comment]) -> None:
        self.s.commentsReady.emit(video, comments)

    def comments_failed(self, video: Video, error: str) -> None:
        self.s.commentsFailed.emit(video, error)

    def status(self, text: str) -> None:
        self.s.status.emit(text)

    def candidates_ready(self) -> None:
        self.s.candidatesReady.emit()


def _shadow(widget: QWidget) -> None:
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(8)
    eff.setOffset(0, 1)
    eff.setColor(QColor(0, 0, 0, 200))
    widget.setGraphicsEffect(eff)


class PlayerView(QGraphicsView):
    """Video surface that also turns wheel/drag/click gestures into feed actions."""

    swipe = Signal(int)  # +1 = next video, -1 = previous
    tapped = Signal()
    resized = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Player")
        self.setScene(QGraphicsScene(self))
        self.video_item = QGraphicsVideoItem()
        self.video_item.setAspectRatioMode(Qt.KeepAspectRatio)
        self.scene().addItem(self.video_item)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setBackgroundBrush(QColor("#000000"))
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setFocusPolicy(Qt.StrongFocus)
        self._wheel = 0
        self._last_swipe = 0.0
        self._press: QPoint | None = None

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        size = self.viewport().size()
        self.scene().setSceneRect(0, 0, size.width(), size.height())
        self.video_item.setPos(0, 0)
        self.video_item.setSize(size.toSizeF())
        self.resized.emit()

    def _emit_swipe(self, direction: int) -> None:
        now = time.monotonic()
        if now - self._last_swipe > 0.35:
            self._last_swipe = now
            self.swipe.emit(direction)

    def wheelEvent(self, event) -> None:  # noqa: N802
        self._wheel += event.angleDelta().y()
        if abs(self._wheel) >= 90:
            self._emit_swipe(1 if self._wheel < 0 else -1)
            self._wheel = 0

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._press = event.position().toPoint()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._press is None:
            return
        dy = event.position().toPoint().y() - self._press.y()
        self._press = None
        if abs(dy) > 60:
            self._emit_swipe(1 if dy < 0 else -1)  # drag up = next, like the phone app
        else:
            self.tapped.emit()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.tapped.emit()  # no double-tap like in MiniTok; it is just a second tap


def _comment_path(r: QRectF) -> QPainterPath:
    """Round speech bubble with a small tail at the bottom left (TikTok's comment icon)."""
    body = QRectF(r.left(), r.top(), r.width(), r.height() * 0.88)
    path = QPainterPath()
    path.addEllipse(body)
    tail = QPainterPath()
    tail.moveTo(body.left() + body.width() * 0.22, body.bottom() - body.height() * 0.22)
    tail.lineTo(body.left() + body.width() * 0.14, r.bottom())
    tail.lineTo(body.left() + body.width() * 0.46, body.bottom() - body.height() * 0.06)
    tail.closeSubpath()
    return path.united(tail)


def _share_path(r: QRectF) -> QPainterPath:
    """Curved arrow pointing right (TikTok's share icon, used here for export)."""
    w, h, x, y = r.width(), r.height(), r.left(), r.top()
    path = QPainterPath()
    path.moveTo(x + w * 0.56, y + h * 0.06)  # top of the arrow head
    path.lineTo(x + w * 0.98, y + h * 0.46)  # tip
    path.lineTo(x + w * 0.56, y + h * 0.86)  # bottom of the arrow head
    path.lineTo(x + w * 0.56, y + h * 0.64)
    path.cubicTo(x + w * 0.32, y + h * 0.62, x + w * 0.12, y + h * 0.74, x + w * 0.02, y + h * 0.96)
    path.cubicTo(x + w * 0.04, y + h * 0.56, x + w * 0.26, y + h * 0.30, x + w * 0.56, y + h * 0.27)
    path.closeSubpath()
    return path


class TikTokAction(QWidget):
    """White filled icon with the count underneath, like the buttons on TikTok's right edge."""

    clicked = Signal()
    ICON = 40

    def __init__(self, kind: str, tooltip: str, parent: QWidget):
        super().__init__(parent)
        self.kind = kind
        self.text = ""
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(64, 70)
        _shadow(self)

    def set_count(self, text: str) -> None:
        self.text = text
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        icon = QRectF((self.width() - self.ICON) / 2, 2, self.ICON, self.ICON)
        p.setBrush(QColor("#ffffff"))
        if self.kind == "comments":
            p.drawPath(_comment_path(icon))
            p.setBrush(QColor("#161823"))
            cy = icon.top() + icon.height() * 0.44
            for dx in (-0.24, 0.0, 0.24):
                p.drawEllipse(QPointF(icon.center().x() + dx * icon.width(), cy), 3.4, 3.4)
        else:
            p.drawPath(_share_path(icon.adjusted(2, 2, -2, -2)))
        font = p.font()
        font.setPixelSize(14)
        font.setWeight(font.Weight.DemiBold)
        p.setFont(font)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(0, icon.bottom() + 4, self.width(), 22), Qt.AlignHCenter | Qt.AlignTop, self.text)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig | None = None, sources: list[Source] | None = None):
        super().__init__()
        self.setWindowTitle("MiniTok")
        self.config = config or AppConfig.load()
        self.cache = Cache(data_dir() / "cache")
        self.cache.clear()  # leftovers from a crash
        self.downloader = Downloader(self.cache, self.config)
        self.events = EventLog(data_dir() / "history.jsonl")
        self.signals = FeedSignals(self)
        self.feed = Feed(
            self.config,
            self.cache,
            self.downloader,
            sources if sources is not None else build_sources(self.config, self.downloader),
            self.events,
            listener=QtListener(self.signals),
        )
        self.stats = WatchStats()
        self._want_next = False
        self._paused_by_user = False
        self._resume_at = 0
        self._failed_skip = QTimer(self, singleShot=True, interval=1600)
        self._failed_skip.timeout.connect(self.go_next)

        self._build_ui()
        self._build_player()
        self._connect()
        self.resize(400, 740)
        self.setMinimumSize(300, 520)
        from .tiktok_connect import TikTokSync

        self.tiktok = TikTokSync(self.downloader, self.events, self)
        self.tiktok.learned.connect(self.on_tiktok_learned, Qt.QueuedConnection)
        self.tiktok.failed.connect(lambda msg: self.show_toast(f"TikTok: {msg}", 4000), Qt.QueuedConnection)
        QTimer.singleShot(0, self.feed.start)
        QTimer.singleShot(1500, self.tiktok.resume_if_pending)  # finish a data request from last time

    # -- construction ---------------------------------------------------------
    def _build_ui(self) -> None:
        self.view = PlayerView(self)
        self.setCentralWidget(self.view)
        v = self.view

        self.tabs = QWidget(v)
        tl = QHBoxLayout(self.tabs)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(18)
        following = QLabel("Following")
        following.setObjectName("TabInactive")
        following.setToolTip("MiniTok only has a For You feed")
        fyp = QLabel("For You")
        fyp.setObjectName("TabActive")
        tl.addWidget(following)
        tl.addWidget(fyp)
        _shadow(self.tabs)

        self.gear = QToolButton(v)
        self.gear.setObjectName("Gear")
        self.gear.setText("⚙")
        self.gear.setToolTip("Algorithm & settings (S)")
        self.gear.setCursor(Qt.PointingHandCursor)
        _shadow(self.gear)

        # the only actions: view comments and export them
        self.comment_btn = TikTokAction("comments", "Comments (C)", v)
        self.export_btn = TikTokAction("export", "Export comments (E)", v)
        self.export_menu = QMenu(self)
        for fmt, label in comments_io.EXPORT_FORMATS.items():
            name = label.split(" (")[0]
            self.export_menu.addAction(f"Export comments as {name}", lambda f=fmt: self.export_comments(f))

        self.caption = QWidget(v)
        cl = QVBoxLayout(self.caption)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        self.author = QLabel()
        self.author.setObjectName("Author")
        self.desc = QLabel()
        self.desc.setObjectName("Caption")
        self.desc.setWordWrap(True)
        self.music = QLabel()
        self.music.setObjectName("Music")
        for w in (self.author, self.desc, self.music):
            cl.addWidget(w)
        _shadow(self.caption)

        self.progress = QProgressBar(v)
        self.progress.setObjectName("Progress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)

        self.status_box = QWidget(v)
        sl = QVBoxLayout(self.status_box)
        sl.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel()
        self.status_label.setObjectName("Status")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.setup_btn = QPushButton("Open settings")
        self.setup_btn.setObjectName("Primary")
        self.setup_btn.setCursor(Qt.PointingHandCursor)
        self.setup_btn.hide()
        sl.addWidget(self.status_label)
        sl.addWidget(self.setup_btn, 0, Qt.AlignHCenter)

        self.play_icon = QLabel("▶", v)
        self.play_icon.setObjectName("BigIcon")
        self.play_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.play_icon.hide()

        self.sheet = CommentsSheet(v)
        self.set_overlay_visible(False)

    def _build_player(self) -> None:
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(float(self.config.volume))
        self.audio.setMuted(bool(self.config.muted))
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.view.video_item)

    def _connect(self) -> None:
        s = self.signals
        q = Qt.QueuedConnection  # always run after the current UI step (e.g. after show_video)
        s.videoReady.connect(self.on_video_ready, q)
        s.videoFailed.connect(self.on_video_failed, q)
        s.commentsReady.connect(self.on_comments_ready, q)
        s.commentsFailed.connect(self.on_comments_failed, q)
        s.status.connect(self.show_status, q)
        s.candidatesReady.connect(self.on_candidates_ready, q)

        self.view.swipe.connect(lambda d: self.go_next() if d > 0 else self.go_previous())
        self.view.tapped.connect(self.toggle_pause)
        self.view.resized.connect(self.layout_overlays)
        self.gear.clicked.connect(self.open_settings)
        self.setup_btn.clicked.connect(self.open_settings)
        self.comment_btn.clicked.connect(self.toggle_comments)
        self.export_btn.clicked.connect(self.show_export_menu)
        self.sheet.closed.connect(self.view.setFocus)

        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.errorOccurred.connect(lambda _e, msg: self.show_status(f"Playback error: {msg}"))

        keys = {
            "Down": self.go_next, "J": self.go_next, "PgDown": self.go_next,
            "Up": self.go_previous, "K": self.go_previous, "PgUp": self.go_previous,
            "Space": self.toggle_pause, "M": self.toggle_mute, "C": self.toggle_comments,
            "E": self.show_export_menu, "I": self.explain, "S": self.open_settings,
            "Esc": self.sheet.hide_animated,
        }
        for key, fn in keys.items():
            QShortcut(QKeySequence(key), self, activated=fn)

    # -- layout ------------------------------------------------------------------
    def layout_overlays(self) -> None:
        w, h = self.view.viewport().width(), self.view.viewport().height()
        self.tabs.adjustSize()
        self.tabs.move((w - self.tabs.width()) // 2, 14)
        self.gear.move(w - 44, 10)

        x = w - 68
        y = int(h * 0.52)
        for btn in (self.comment_btn, self.export_btn):
            btn.move(x, y)
            y += btn.height() + 14

        cap_w = w - 84
        self.caption.setFixedWidth(cap_w)
        self.caption.adjustSize()
        self.caption.move(12, h - self.caption.height() - 16)

        self.progress.setGeometry(0, h - 2, w, 2)
        self.status_box.setFixedWidth(min(w - 50, 320))
        self.status_box.adjustSize()
        self.status_box.move((w - self.status_box.width()) // 2, (h - self.status_box.height()) // 2)
        self.play_icon.adjustSize()
        self.play_icon.move((w - self.play_icon.width()) // 2, (h - self.play_icon.height()) // 2)
        self.sheet.reposition()

    def set_overlay_visible(self, visible: bool) -> None:
        for wdg in (self.comment_btn, self.export_btn, self.caption):
            wdg.setVisible(visible)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(400, 740)

    # -- feed navigation ------------------------------------------------------------
    def _stop_player(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())  # release the file so the feed can delete it

    def _collect_stats(self) -> WatchStats:
        self.stats.watched_seconds = max(0.0, self.player.position() / 1000.0)
        return self.stats

    def go_next(self) -> None:
        self._failed_skip.stop()
        if not self.feed.can_advance():
            self._want_next = True
            self.feed.request_more()
            self.show_status("Loading more videos…")
            return
        stats = self._collect_stats() if self.feed.current else None
        position = self.player.position()
        self._stop_player()
        video = self.feed.next(stats)
        if video is None:
            # nothing pickable after all (e.g. everything left is blocked); resume the current one
            self._want_next = True
            if self.feed.current is not None:
                self.show_video(self.feed.current, keep_stats=True, position=position)
            self.show_status("Loading more videos…")
            return
        self.show_video(video)

    def go_previous(self) -> None:
        if self.feed.index <= 0:
            return
        self._failed_skip.stop()
        stats = self._collect_stats()
        self._stop_player()
        video = self.feed.previous(stats)
        if video is not None:
            self.show_video(video)

    def on_candidates_ready(self) -> None:
        if self.feed.current is None or self._want_next:
            self._want_next = False
            self.go_next()

    # -- showing a video ----------------------------------------------------------------
    def show_video(self, video: Video, keep_stats: bool = False, position: int = 0) -> None:
        if not keep_stats:
            self.stats = WatchStats()
        self._paused_by_user = False
        self.play_icon.hide()
        self.progress.setValue(0)
        self.set_overlay_visible(True)
        self.author.setText(f"@{video.author}" if video.author else "")
        desc = video.description.strip()
        self.desc.setText(desc if len(desc) < 150 else desc[:147] + "…")
        self.desc.setVisible(bool(desc))
        self.music.setText(f"♪  {video.music}" if video.music else "♪  original sound")
        self._set_counts(video)
        self._resume_at = position
        self.show_status("Loading…")
        if self.sheet.isVisible():
            self.sheet.set_loading(video)
        self.layout_overlays()
        self.view.setFocus()

    def on_video_ready(self, video: Video, path: Path) -> None:
        cur = self.feed.current
        if cur is None or cur.id != video.id:
            return
        self.hide_status()
        if self.player.source() != QUrl.fromLocalFile(str(path)):
            self.player.setSource(QUrl.fromLocalFile(str(path)))
        if self._resume_at:
            self.player.setPosition(self._resume_at)
            self._resume_at = 0
        self.player.play()
        self._set_counts(video)  # the download may have refreshed the numbers

    def on_video_failed(self, video: Video, error: str) -> None:
        cur = self.feed.current
        if cur is None or cur.id != video.id:
            return
        self.show_status(f"Couldn't load this video - skipping.\n\n{error}")
        self._failed_skip.start()

    def on_position(self, pos: int) -> None:
        dur = self.player.duration()
        if dur > 0:
            self.progress.setValue(int(pos * 1000 / dur))

    def on_duration(self, dur: int) -> None:
        video = self.feed.current
        if video is not None and dur > 0 and not video.duration:
            video.duration = dur / 1000.0

    def on_media_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia:
            self.stats.loops += 1  # TikTok loops; every full loop is a strong "I like this" signal
            self.player.setPosition(0)
            self.player.play()

    # -- status overlay ----------------------------------------------------------------
    def show_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.setup_btn.setVisible("Settings" in text or "⚙" in text)
        self.status_box.show()
        self.status_box.raise_()
        self.layout_overlays()

    def hide_status(self) -> None:
        self.status_box.hide()

    def _set_counts(self, video: Video) -> None:
        self.comment_btn.set_count(tiktok_count(video.comment_count))
        self.export_btn.set_count(tiktok_count(video.shares))

    # -- actions ---------------------------------------------------------------------------
    def toggle_pause(self) -> None:
        if self.feed.current is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self._paused_by_user = True
            self.play_icon.show()
            self.play_icon.raise_()
        else:
            self.player.play()
            self._paused_by_user = False
            self.play_icon.hide()

    def toggle_mute(self) -> None:
        self.config.muted = not self.audio.isMuted()
        self.audio.setMuted(self.config.muted)
        self.show_toast("Muted" if self.config.muted else "Sound on")

    def show_toast(self, text: str, ms: int = 1500) -> None:
        self.show_status(text)
        QTimer.singleShot(ms, lambda: self.status_label.text() == text and self.hide_status())

    def show_export_menu(self) -> None:
        if self.feed.current is None:
            return
        self.export_menu.popup(self.export_btn.mapToGlobal(QPoint(-190, 10)))

    def export_comments(self, fmt: str) -> None:
        video = self.feed.current
        if video is None:
            return
        comments = self.cache.load_comments(video.id)
        if comments is None:
            self.show_toast("Comments are still downloading - try again in a moment")
            self.feed.comments_for_current()
            return
        if not comments:
            self.show_toast("This video has no comments to export")
            return
        name = f"comments_{video.author or 'video'}_{video.id}.{fmt}".replace("/", "_")
        path, _ = QFileDialog.getSaveFileName(self, "Export comments", name, comments_io.EXPORT_FORMATS[fmt])
        if not path:
            return
        try:
            comments_io.export(video, comments, path, fmt)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.show_toast(f"Exported {len(comments)} comments")

    def toggle_comments(self) -> None:
        if self.sheet.isVisible():
            self.sheet.hide_animated()
            return
        video = self.feed.current
        if video is None:
            return
        self.stats.opened_comments = True
        cached = self.cache.load_comments(video.id)
        if cached is not None:
            self.sheet.set_comments(video, cached)
        else:
            self.sheet.set_loading(video)
            self.feed.comments_for_current()
        self.sheet.show_animated()

    def on_comments_ready(self, video: Video, comments: list) -> None:
        cur = self.feed.current
        if cur is not None and cur.id == video.id and self.sheet.isVisible():
            self.sheet.set_comments(video, comments)

    def on_comments_failed(self, video: Video, error: str) -> None:
        cur = self.feed.current
        if cur is not None and cur.id == video.id and self.sheet.isVisible():
            self.sheet.set_error(video, error)

    def explain(self) -> None:
        scored = self.feed.current_scored
        if scored is None:
            return
        algo = self.config.algorithm
        preset = PRESETS.get(algo.preset)
        lines = [f"<b>Algorithm:</b> {preset.name if preset else algo.preset}"]
        if scored.explored:
            lines.append(f"🎲 Picked at random by exploration ({algo.exploration:.0%} of the time).")
        lines.append(f"<b>Score:</b> {scored.score:.2f}")
        rows = []
        for name, value in sorted(scored.features.items(), key=lambda kv: -abs(kv[1] * algo.weights.get(kv[0], 0))):
            if algo.preset == "formula":
                rows.append(f"<tr><td>{name}</td><td align=right>{value:.2f}</td><td></td></tr>")
            else:
                wgt = algo.weights.get(name, 0.0)
                rows.append(f"<tr><td>{name}</td><td align=right>{value:.2f}</td>"
                            f"<td align=right>× {wgt:+.1f} = <b>{value * wgt:+.2f}</b></td></tr>")
        lines.append("<table cellspacing=4>" + "".join(rows) + "</table>")
        if algo.preset == "formula":
            lines.append(f"<b>Formula:</b> <code>{algo.formula}</code>")
        matched = [t for t in scored.video.tokens() if t in algo.interests]
        if matched:
            lines.append("<b>Matched interests:</b> " + ", ".join(f"{t} ({algo.interests[t]:+.1f})" for t in matched[:8]))
        if algo.diversity and scored.features.get("novelty", 1) < 1:
            lines.append(f"Creator seen recently: −{algo.diversity * (1 - scored.features['novelty']) * 2:.2f}")
        box = QMessageBox(self)
        box.setWindowTitle("Why am I seeing this?")
        box.setTextFormat(Qt.RichText)
        box.setText("<br>".join(lines))
        box.exec()

    def open_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        was_playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.player.pause()
        dlg = SettingsDialog(self.config, self.feed, self.downloader, self.events, self.cache, self,
                             tiktok=self.tiktok)
        accepted = dlg.exec()
        dlg.deleteLater()  # disconnects it from the TikTok sync signals
        if accepted:
            sources_changed = dlg.apply_to(self.config)
            self.config.save()
            self.feed.reload_algorithm()
            if sources_changed:
                self.feed.reload_sources(build_sources(self.config, self.downloader))
                if self.feed.current is None:
                    self.show_status("Finding videos…")
            self.layout_overlays()
        if was_playing and not self._paused_by_user:
            self.player.play()

    def on_tiktok_learned(self, export, result) -> None:
        """TikTok's data arrived and was fitted: switch the feed to it, no questions asked."""
        self.config.algorithm.apply_learned(result)
        try:
            self.config.save()
        except OSError:
            pass
        self.feed.reload_algorithm()
        watched = len(export.watched)
        self.show_toast(f"Your algorithm is ready - learned from {watched:,} videos you watched on TikTok", 4500)

    # -- lifecycle -----------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        self.tiktok.stop()  # a pending TikTok request is resumed on the next start
        stats = self._collect_stats() if self.feed.current else None
        self._stop_player()
        self.feed.shutdown(stats)
        self.config.volume = self.audio.volume()
        try:
            self.config.save()
        except OSError:
            pass
        super().closeEvent(event)
