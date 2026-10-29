"""The comments bottom sheet: view and export only - there is no way to post, like or reply."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import comments as comments_io
from ..config import COMMENT_LIMIT
from ..models import Comment, Video, human_count


def _ago(ts: float) -> str:
    if not ts:
        return ""
    delta = datetime.now().timestamp() - ts
    for secs, unit in ((86400 * 365, "y"), (86400 * 7, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if delta >= secs:
            return f"{int(delta // secs)}{unit} ago"
    return "just now"


class CommentRow(QWidget):
    def __init__(self, c: Comment, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(10)
        avatar = QLabel((c.author[:1] or "?").upper())
        avatar.setObjectName("CAvatar")
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignCenter)
        row.addWidget(avatar, 0, Qt.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(2)
        author = QLabel(c.author)
        author.setObjectName("CAuthor")
        text = QLabel(c.text)
        text.setObjectName("CText")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)  # select to copy - still read-only
        meta_parts = [p for p in (_ago(c.created), f"{c.reply_count} replies" if c.reply_count else "") if p]
        body.addWidget(author)
        body.addWidget(text)
        if meta_parts:
            meta = QLabel(" · ".join(meta_parts))
            meta.setObjectName("CMeta")
            body.addWidget(meta)
        row.addLayout(body, 1)

        likes = QLabel(f"♡\n{human_count(c.likes)}")
        likes.setObjectName("CMeta")
        likes.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        row.addWidget(likes, 0, Qt.AlignTop)


class CommentsSheet(QFrame):
    closed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Sheet")
        self.video: Video | None = None
        self.comments: list[Comment] = []
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._after_anim)
        self._closing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(12, 2, 8, 2)
        self.export_btn = QPushButton("Export")
        self.export_btn.setObjectName("Export")
        self.export_btn.setCursor(Qt.PointingHandCursor)
        menu = QMenu(self.export_btn)
        for fmt, label in comments_io.EXPORT_FORMATS.items():
            menu.addAction(label.split(" (")[0], lambda f=fmt: self.export(f))
        self.export_btn.setMenu(menu)
        self.title = QLabel("Comments")
        self.title.setObjectName("SheetTitle")
        self.title.setAlignment(Qt.AlignCenter)
        close = QToolButton()
        close.setText("✕")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.hide_animated)
        head.addWidget(self.export_btn)
        head.addWidget(self.title, 1)
        head.addWidget(close)
        outer.addLayout(head)

        self.note = QLabel(f"Read-only · up to {COMMENT_LIMIT} · deleted when you swipe to the next video")
        self.note.setObjectName("SheetNote")
        self.note.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)
        self._set_body(QLabel("Loading comments…"))
        self.hide()

    # -- content ---------------------------------------------------------------
    def _set_body(self, widget: QWidget) -> None:
        holder = QWidget()
        holder.setObjectName("CommentList")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 4, 0, 12)
        lay.setSpacing(0)
        if isinstance(widget, QLabel):
            widget.setAlignment(Qt.AlignCenter)
            widget.setObjectName("SheetNote")
            widget.setWordWrap(True)
            lay.addSpacing(30)
        lay.addWidget(widget)
        lay.addStretch(1)
        self.scroll.setWidget(holder)

    def set_loading(self, video: Video | None) -> None:
        self.video = video
        self.comments = []
        self.title.setText("Comments")
        self.export_btn.setEnabled(False)
        self._set_body(QLabel("Loading comments…"))

    def set_comments(self, video: Video, comments: list[Comment]) -> None:
        self.video = video
        self.comments = list(comments)
        total = f" of {human_count(video.comment_count)}" if video.comment_count > len(comments) else ""
        self.title.setText(f"{len(comments)}{total} comments")
        self.export_btn.setEnabled(bool(comments))
        if not comments:
            self._set_body(QLabel("No comments on this video."))
            return
        holder = QWidget()
        holder.setObjectName("CommentList")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 4, 0, 12)
        lay.setSpacing(0)
        for c in comments:
            lay.addWidget(CommentRow(c))
        lay.addStretch(1)
        self.scroll.setWidget(holder)

    def set_error(self, video: Video, error: str) -> None:
        self.video = video
        self.comments = []
        self.export_btn.setEnabled(False)
        self.title.setText("Comments")
        self._set_body(QLabel(f"Couldn't load comments.\n{error}"))

    def export(self, fmt: str) -> None:
        if not self.video or not self.comments:
            return
        name = f"comments_{self.video.author or 'video'}_{self.video.id}.{fmt}".replace("/", "_")
        path, _ = QFileDialog.getSaveFileName(self, "Export comments", name, comments_io.EXPORT_FORMATS[fmt])
        if not path:
            return
        try:
            comments_io.export(self.video, self.comments, path, fmt)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    # -- animation ----------------------------------------------------------------
    def _target(self) -> QRect:
        p = self.parentWidget()
        h = int(p.height() * 0.68)
        return QRect(0, p.height() - h, p.width(), h)

    def show_animated(self) -> None:
        self._closing = False
        target = self._target()
        start = QRect(target.x(), self.parentWidget().height(), target.width(), target.height())
        self.setGeometry(start)
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(target)
        self._anim.start()

    def hide_animated(self) -> None:
        if not self.isVisible():
            return
        self._closing = True
        end = QRect(self.x(), self.parentWidget().height(), self.width(), self.height())
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(end)
        self._anim.start()

    def _after_anim(self) -> None:
        if self._closing:
            self.hide()
            self.closed.emit()

    def reposition(self) -> None:
        if self.isVisible() and self._anim.state() != QPropertyAnimation.Running:
            self.setGeometry(self._target())
