"""The ⚙ settings opened from the For You page: choose/edit the algorithm and teach it your data."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..algorithm import learner
from ..algorithm.features import FEATURE_NAMES, FEATURES
from ..algorithm.formula import Formula, FormulaError, help_text
from ..algorithm.presets import PRESETS
from ..algorithm.ranker import Ranker
from ..algorithm.tiktok_export import ExportData, load_export
from ..config import COMMENT_LIMIT, AlgorithmConfig, AppConfig, data_dir
from ..downloader import Downloader
from ..events import EventLog
from ..feed import Feed
from ..models import Video
from ..storage import Cache

BROWSERS = ["", "chrome", "firefox", "edge", "brave", "safari", "chromium", "opera", "vivaldi"]


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Hint")
    lbl.setWordWrap(True)
    return lbl


def _heading(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("H")
    return lbl


class _Worker(QObject):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, feed: Feed, downloader: Downloader, events: EventLog,
                 cache: Cache, parent: QWidget | None = None, tiktok=None):
        super().__init__(parent)
        self.setWindowTitle("For You algorithm & settings")
        self.resize(560, 700)
        self.feed = feed
        self.downloader = downloader
        self.events = events
        self.cache = cache
        self.config = config
        self.tiktok = tiktok  # ui.tiktok_connect.TikTokSync, owned by the main window
        self.original_sources = (list(config.sources), config.local_folder)
        self.algo = AlgorithmConfig.from_dict(copy.deepcopy(config.algorithm.to_dict()))
        self.app = copy.deepcopy(config)
        self.export_data: ExportData | None = None
        self.result: learner.LearnResult | None = None
        self._stop = threading.Event()
        self._updating = False

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._scroll(self._algorithm_tab()), "Algorithm")
        self.tabs.addTab(self._interests_tab(), "Interests")
        self.tabs.addTab(self._scroll(self._data_tab()), "Find my algorithm")
        self.tabs.addTab(self._scroll(self._sources_tab()), "Sources")
        self.tabs.addTab(self._scroll(self._storage_tab()), "Storage")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("Primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._load_algo_into_widgets()

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        return area

    # ======================================================================== Algorithm
    def _algorithm_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        lay.addWidget(_heading("Type of algorithm"))
        self.preset_box = QComboBox()
        for key, p in PRESETS.items():
            self.preset_box.addItem(p.name, key)
        self.preset_box.currentIndexChanged.connect(self._preset_changed)
        lay.addWidget(self.preset_box)
        self.preset_desc = _hint("")
        lay.addWidget(self.preset_desc)

        # weights
        self.weights_group = QGroupBox("Weights - how much each signal counts")
        grid = QGridLayout(self.weights_group)
        self.sliders: dict[str, QSlider] = {}
        self.slider_values: dict[str, QLabel] = {}
        for row, name in enumerate(FEATURE_NAMES):
            label = QLabel(name.replace("_", " "))
            label.setToolTip(FEATURES[name])
            slider = QSlider(Qt.Horizontal)
            slider.setRange(-50, 50)  # -5.0 .. 5.0
            slider.setToolTip(FEATURES[name])
            value = QLabel("0.0")
            value.setFixedWidth(40)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            slider.valueChanged.connect(lambda v, n=name: self._weight_changed(n, v))
            grid.addWidget(label, row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(value, row, 2)
            self.sliders[name] = slider
            self.slider_values[name] = value
        grid.addWidget(_hint("Negative weights push videos with that signal down. "
                             "Hover a name for what it measures."), len(FEATURE_NAMES), 0, 1, 3)
        lay.addWidget(self.weights_group)

        # formula
        self.formula_group = QGroupBox("Your formula (higher score = shown sooner)")
        fl = QVBoxLayout(self.formula_group)
        self.formula_edit = QPlainTextEdit()
        self.formula_edit.setFont(QFont("monospace"))
        self.formula_edit.setFixedHeight(70)
        self.formula_edit.textChanged.connect(lambda: self.formula_status.setText(""))
        fl.addWidget(self.formula_edit)
        row = QHBoxLayout()
        check = QPushButton("Check formula")
        check.clicked.connect(self._check_formula)
        helpb = QPushButton("Variables & functions…")
        helpb.clicked.connect(lambda: QMessageBox.information(self, "Formula help", help_text()))
        self.formula_status = QLabel("")
        self.formula_status.setWordWrap(True)
        row.addWidget(check)
        row.addWidget(helpb)
        row.addStretch(1)
        fl.addLayout(row)
        fl.addWidget(self.formula_status)
        fl.addWidget(_hint('Example: 3*interest + like_rate + (2 if has("#cats") else 0) - 0.5*popularity'))
        lay.addWidget(self.formula_group)

        # behaviour
        beh = QGroupBox("Behaviour")
        form = QFormLayout(beh)
        self.explore = QSlider(Qt.Horizontal)
        self.explore.setRange(0, 100)
        self.explore_lbl = QLabel()
        self.explore.valueChanged.connect(lambda v: self.explore_lbl.setText(f"{v}% random picks"))
        er = QHBoxLayout()
        er.addWidget(self.explore, 1)
        er.addWidget(self.explore_lbl)
        form.addRow("Exploration", er)
        self.diversity = QSlider(Qt.Horizontal)
        self.diversity.setRange(0, 100)
        self.diversity_lbl = QLabel()
        self.diversity.valueChanged.connect(lambda v: self.diversity_lbl.setText(f"{v}%"))
        dr = QHBoxLayout()
        dr.addWidget(self.diversity, 1)
        dr.addWidget(self.diversity_lbl)
        form.addRow("Creator variety", dr)
        self.pref_len = QSpinBox()
        self.pref_len.setRange(3, 900)
        self.pref_len.setSuffix(" s")
        form.addRow("Preferred length", self.pref_len)
        self.learn_check = QCheckBox("Learn my interests while I watch (finish/loop = more, skip = less)")
        form.addRow(self.learn_check)
        lay.addWidget(beh)

        # preview + share
        prev = QGroupBox("Preview - what would come next with these settings")
        pl = QVBoxLayout(prev)
        self.preview_list = QListWidget()
        self.preview_list.setFixedHeight(150)
        pb = QPushButton("Rank the videos waiting in the feed")
        pb.clicked.connect(self._preview)
        pl.addWidget(pb)
        pl.addWidget(self.preview_list)
        lay.addWidget(prev)

        share = QHBoxLayout()
        imp = QPushButton("Import algorithm…")
        imp.clicked.connect(self._import_algo)
        exp = QPushButton("Export algorithm…")
        exp.clicked.connect(self._export_algo)
        share.addWidget(imp)
        share.addWidget(exp)
        share.addStretch(1)
        lay.addLayout(share)
        lay.addStretch(1)
        return w

    def _load_algo_into_widgets(self) -> None:
        self._updating = True
        a = self.algo
        self.preset_box.setCurrentIndex(max(0, self.preset_box.findData(a.preset)))
        self.preset_desc.setText(PRESETS[a.preset].description)
        for name, slider in self.sliders.items():
            v = float(a.weights.get(name, 0.0))
            slider.setValue(round(v * 10))
            self.slider_values[name].setText(f"{v:+.1f}")
        self.formula_edit.setPlainText(a.formula)
        self.explore.setValue(round(a.exploration * 100))
        self.explore_lbl.setText(f"{round(a.exploration * 100)}% random picks")
        self.diversity.setValue(round(a.diversity * 100))
        self.diversity_lbl.setText(f"{round(a.diversity * 100)}%")
        self.pref_len.setValue(int(a.preferred_duration or 25))
        self.learn_check.setChecked(a.learn_while_watching)
        is_formula = a.preset == "formula"
        self.formula_group.setVisible(is_formula)
        self.weights_group.setVisible(not is_formula)
        self._updating = False
        if hasattr(self, "interest_table"):
            self._fill_interests()

    def _preset_changed(self) -> None:
        if self._updating:
            return
        self._read_algo_widgets()
        key = self.preset_box.currentData()
        if key == "learned" and not self.algo.learned_weights:
            QMessageBox.information(
                self, "Nothing learned yet",
                "Open the 'Find my algorithm' tab and fit your data first. Until then this uses default weights.")
        self.algo.apply_preset(key)
        self._load_algo_into_widgets()

    def _weight_changed(self, name: str, value: int) -> None:
        self.slider_values[name].setText(f"{value / 10:+.1f}")
        if self._updating:
            return
        if self.preset_box.currentData() not in ("custom",):
            # hand-tuning turns any preset into your own custom one
            self._updating = True
            self.preset_box.setCurrentIndex(self.preset_box.findData("custom"))
            self.preset_desc.setText(PRESETS["custom"].description)
            self._updating = False

    def _read_algo_widgets(self) -> None:
        a = self.algo
        a.preset = self.preset_box.currentData()
        a.weights = {n: s.value() / 10.0 for n, s in self.sliders.items()}
        a.formula = self.formula_edit.toPlainText().strip()
        a.exploration = self.explore.value() / 100.0
        a.diversity = self.diversity.value() / 100.0
        a.preferred_duration = float(self.pref_len.value())
        a.learn_while_watching = self.learn_check.isChecked()
        if hasattr(self, "interest_table"):
            a.interests = self._read_interests()
            a.blocked = [ln.strip() for ln in self.blocked_edit.toPlainText().splitlines() if ln.strip()]

    def _check_formula(self) -> bool:
        try:
            f = Formula(self.formula_edit.toPlainText())
            f.evaluate({n: 0.5 for n in FEATURE_NAMES}, Video(id="t", url="", views=1000, duration=20))
        except FormulaError as exc:
            self.formula_status.setText(f"❌ {exc}")
            return False
        self.formula_status.setText("✅ Formula is valid")
        return True

    def _preview(self) -> None:
        self._read_algo_widgets()
        self.preview_list.clear()
        if self.algo.preset == "formula" and not self._check_formula():
            return
        cfg = copy.deepcopy(self.algo)
        cfg.exploration = 0.0
        with self.feed._lock:
            candidates = list(self.feed.pool.values()) + [s.video for s in self.feed.queue]
            recent = self.feed._recent_authors()
        if not candidates:
            self.preview_list.addItem("No videos waiting in the feed yet.")
            return
        for s in Ranker(cfg).rank(candidates, recent)[:15]:
            v = s.video
            self.preview_list.addItem(f"{s.score:+6.2f}  @{v.author}  {v.description[:60]}")

    def _export_algo(self) -> None:
        self._read_algo_widgets()
        path, _ = QFileDialog.getSaveFileName(self, "Export algorithm", "my_algorithm.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.algo.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def _import_algo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import algorithm", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.algo = AlgorithmConfig.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        self._load_algo_into_widgets()

    # ======================================================================== Interests
    def _interests_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(_heading("Interest profile"))
        lay.addWidget(_hint("#hashtag, @creator, ♪sound or a caption word, with a weight from -3 (never) to +3 "
                            "(love it). This feeds the 'interest' signal and updates by itself as you watch."))
        self.interest_table = QTableWidget(0, 2)
        self.interest_table.setHorizontalHeaderLabels(["Token", "Weight"])
        self.interest_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.interest_table.verticalHeader().setVisible(False)
        self.interest_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.interest_table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self._add_interest)
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self._remove_interest)
        reset = QPushButton("Reset all")
        reset.clicked.connect(lambda: self.interest_table.setRowCount(0))
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        row.addWidget(reset)
        lay.addLayout(row)
        lay.addWidget(_heading("Never show"))
        lay.addWidget(_hint("One per line: a word, #hashtag or @creator."))
        self.blocked_edit = QPlainTextEdit("\n".join(self.algo.blocked))
        self.blocked_edit.setFixedHeight(90)
        lay.addWidget(self.blocked_edit)
        self._fill_interests()
        return w

    def _fill_interests(self) -> None:
        items = sorted(self.algo.interests.items(), key=lambda kv: kv[1], reverse=True)
        self.interest_table.setRowCount(0)
        for tok, val in items:
            self._append_interest(tok, val)

    def _append_interest(self, tok: str, val: float) -> None:
        r = self.interest_table.rowCount()
        self.interest_table.insertRow(r)
        self.interest_table.setItem(r, 0, QTableWidgetItem(tok))
        self.interest_table.setItem(r, 1, QTableWidgetItem(f"{val:+.2f}"))

    def _add_interest(self) -> None:
        self._append_interest("#", 1.0)
        r = self.interest_table.rowCount() - 1
        self.interest_table.scrollToBottom()
        self.interest_table.editItem(self.interest_table.item(r, 0))

    def _remove_interest(self) -> None:
        for r in sorted({i.row() for i in self.interest_table.selectedIndexes()}, reverse=True):
            self.interest_table.removeRow(r)

    def _read_interests(self) -> dict[str, float]:
        out = {}
        for r in range(self.interest_table.rowCount()):
            tok_item, val_item = self.interest_table.item(r, 0), self.interest_table.item(r, 1)
            tok = (tok_item.text() if tok_item else "").strip().lower()
            if not tok or tok in ("#", "@"):
                continue
            try:
                val = float((val_item.text() if val_item else "0").replace(",", "."))
            except ValueError:
                continue
            out[tok] = max(-3.0, min(3.0, val))
        return out

    # ======================================================================== Find my algorithm
    def _data_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(_heading("Find my algorithm from my own data"))
        lay.addWidget(_hint(
            "MiniTok fits a model that explains what you actually finish, rewatch and like - which signals "
            "matter to you, how much, and which topics/creators. It uses your TikTok data export and/or what "
            "you watched in MiniTok. Everything stays on your computer.\n"
            "Note: TikTok's own recommender is private, so no app can copy it exactly; this recovers your "
            "personal ranking as closely as your data allows."))

        if self.tiktok is not None:
            g0 = QGroupBox("The easy way")
            l0 = QVBoxLayout(g0)
            l0.addWidget(_hint("Tap once and approve in your browser. TikTok sends MiniTok your watch history and "
                               "likes, and your algorithm updates by itself when it arrives. Nothing to download."))
            row = QHBoxLayout()
            self.connect_btn = QPushButton("Continue with TikTok")
            self.connect_btn.setObjectName("Primary")
            self.connect_btn.clicked.connect(self._connect_tiktok)
            row.addWidget(self.connect_btn)
            row.addStretch(1)
            l0.addLayout(row)
            self.connect_status = _hint(self.tiktok.last_status)
            l0.addWidget(self.connect_status)
            lay.addWidget(g0)
            self.tiktok.status.connect(self._tiktok_status)
            self.tiktok.failed.connect(self._tiktok_status)
            self.tiktok.learned.connect(self._tiktok_learned)
            self._refresh_connect_button()

        g1 = QGroupBox("Or: a TikTok data export file")
        l1 = QVBoxLayout(g1)
        l1.addWidget(_hint("Get it in TikTok: Settings › Account › Download your data (JSON recommended). "
                           "Load the .zip, the .json, or the unzipped folder."))
        r = QHBoxLayout()
        load_file = QPushButton("Load export file…")
        load_file.clicked.connect(self._load_export_file)
        load_dir = QPushButton("Load unzipped folder…")
        load_dir.clicked.connect(self._load_export_dir)
        r.addWidget(load_file)
        r.addWidget(load_dir)
        r.addStretch(1)
        l1.addLayout(r)
        self.export_summary = _hint("No export loaded.")
        l1.addWidget(self.export_summary)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Look up details of"))
        self.lookup = QSpinBox()
        self.lookup.setRange(0, 500)
        self.lookup.setValue(60)
        r2.addWidget(self.lookup)
        r2.addWidget(QLabel("videos from it (internet)"))
        r2.addStretch(1)
        l1.addLayout(r2)
        l1.addWidget(_hint("The export only contains links. Looking up a sample of them (views, likes, "
                           "hashtags, creator, length) is what lets MiniTok learn weights from it. 0 = only "
                           "use searches, follows and favourite hashtags."))
        self.follow_btn = QPushButton("Add the creators I follow as sources")
        self.follow_btn.setEnabled(False)
        self.follow_btn.clicked.connect(self._follows_to_sources)
        l1.addWidget(self.follow_btn)
        lay.addWidget(g1)

        g2 = QGroupBox("2. MiniTok watch history")
        l2 = QVBoxLayout(g2)
        self.history_count = self.events.count()
        self.history_lbl = QLabel(f"{self.history_count} videos watched in MiniTok so far.")
        l2.addWidget(self.history_lbl)
        r3 = QHBoxLayout()
        self.use_history = QCheckBox("Use it")
        self.use_history.setChecked(True)
        clear = QPushButton("Delete history")
        clear.clicked.connect(self._clear_history)
        r3.addWidget(self.use_history)
        r3.addStretch(1)
        r3.addWidget(clear)
        l2.addLayout(r3)
        lay.addWidget(g2)

        r4 = QHBoxLayout()
        self.fit_btn = QPushButton("Find my algorithm")
        self.fit_btn.setObjectName("Primary")
        self.fit_btn.clicked.connect(self._fit)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop.set)
        r4.addWidget(self.fit_btn)
        r4.addWidget(self.stop_btn)
        r4.addStretch(1)
        lay.addLayout(r4)
        self.fit_progress = QProgressBar()
        self.fit_progress.setVisible(False)
        lay.addWidget(self.fit_progress)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMinimumHeight(180)
        self.report.setPlaceholderText("Results appear here.")
        lay.addWidget(self.report)
        self.apply_btn = QPushButton("Use this as my algorithm")
        self.apply_btn.setObjectName("Primary")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply_result)
        lay.addWidget(self.apply_btn)
        lay.addStretch(1)
        return w

    def _connect_tiktok(self) -> None:
        from .tiktok_connect import DeveloperAppDialog

        if not self.tiktok.state.configured:
            if not DeveloperAppDialog(self.tiktok.state, self).exec():
                return
        self.tiktok.start(login=True)
        self._refresh_connect_button()

    def _refresh_connect_button(self) -> None:
        busy = self.tiktok.busy
        self.connect_btn.setEnabled(not busy)
        connected = self.tiktok.state.token is not None
        self.connect_btn.setText("Working…" if busy else ("Update from TikTok" if connected else "Continue with TikTok"))

    def _tiktok_status(self, text: str) -> None:
        self.connect_status.setText(text)
        self._refresh_connect_button()

    def _tiktok_learned(self, _export, result) -> None:
        # the main window has already applied it to the live config; show it here too
        self.algo = AlgorithmConfig.from_dict(copy.deepcopy(self.config.algorithm.to_dict()))
        self._load_algo_into_widgets()
        self.report.setPlainText("\n".join(result.report))
        self.connect_status.setText(self.tiktok.last_status)
        self._refresh_connect_button()

    def _load_export_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "TikTok data export", "",
                                              "TikTok export (*.zip *.json *.txt);;All files (*)")
        if path:
            self._load_export(path)

    def _load_export_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Unzipped TikTok export folder")
        if path:
            self._load_export(path)

    def _load_export(self, path: str) -> None:
        try:
            data = load_export(path)
        except Exception as exc:  # noqa: BLE001 - zip/json errors of all kinds
            QMessageBox.warning(self, "Could not read export", str(exc))
            return
        if data.is_empty():
            QMessageBox.warning(self, "Nothing found", "That file doesn't look like a TikTok data export.")
            return
        self.export_data = data
        s = data.summary()
        parts = [f"{v} {k.replace('_', ' ')}" for k, v in s.items() if v]
        stats = data.watch_stats()
        text = "Loaded: " + ", ".join(parts) + "."
        if stats:
            text += (f"\nEstimated from watch timestamps: median {stats['median_watch_seconds']:.0f}s per video, "
                     f"{stats['skip_rate']:.0%} skipped within 3s.")
        self.export_summary.setText(text)
        self.follow_btn.setEnabled(bool(data.following))

    def _follows_to_sources(self) -> None:
        if not self.export_data:
            return
        current = [ln.strip() for ln in self.sources_edit.toPlainText().splitlines() if ln.strip()]
        names = [f"@{r.text.lstrip('@')}" for r in self.export_data.following if r.text][-30:]
        merged = list(dict.fromkeys(current + names))
        self.sources_edit.setPlainText("\n".join(merged))
        QMessageBox.information(self, "Sources", f"Added {len(merged) - len(current)} creators "
                                                 "(the 30 you followed most recently). See the Sources tab.")

    def _clear_history(self) -> None:
        if QMessageBox.question(self, "Delete history", "Delete your MiniTok watch history?") == QMessageBox.Yes:
            self.events.clear()
            self.history_count = 0
            self.history_lbl.setText("0 videos watched in MiniTok so far.")

    def _fit(self) -> None:
        use_hist = self.use_history.isChecked() and self.history_count > 0
        if not use_hist and self.export_data is None:
            QMessageBox.information(self, "No data", "Load a TikTok export or watch some videos in MiniTok first.")
            return
        self._stop.clear()
        self.fit_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.apply_btn.setEnabled(False)
        self.fit_progress.setVisible(True)
        self.fit_progress.setRange(0, 0)
        self.report.setPlainText("Working…")
        worker = _Worker(self)
        worker.progress.connect(self._fit_progress)
        worker.done.connect(self._fit_done)
        worker.failed.connect(self._fit_failed)
        export_data, lookup = self.export_data, self.lookup.value()

        def run() -> None:
            try:
                examples = learner.examples_from_history(self.events) if use_hist else []
                seeds, stats = {}, None
                if export_data is not None:
                    seeds = export_data.seed_interests()
                    stats = export_data.watch_stats()
                    if lookup > 0:
                        examples += learner.examples_from_export(
                            export_data, self.downloader.metadata, sample=lookup,
                            progress=worker.progress.emit, should_stop=self._stop.is_set)
                worker.done.emit(learner.learn(examples, seeds, stats))
            except Exception as exc:  # noqa: BLE001
                worker.failed.emit(str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _fit_progress(self, i: int, n: int, vid: str) -> None:
        self.fit_progress.setRange(0, max(1, n))
        self.fit_progress.setValue(i)
        if vid:
            self.report.setPlainText(f"Looking up video {i + 1} of {n} from your export…")

    def _fit_done(self, result: learner.LearnResult) -> None:
        self.result = result
        self.fit_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.fit_progress.setVisible(False)
        self.report.setPlainText("\n".join(result.report))
        self.apply_btn.setEnabled(True)

    def _fit_failed(self, error: str) -> None:
        self.fit_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.fit_progress.setVisible(False)
        self.report.setPlainText(f"Failed: {error}")

    def _apply_result(self) -> None:
        if not self.result:
            return
        self._read_algo_widgets()
        self.algo.apply_learned(self.result)
        self._load_algo_into_widgets()
        self.tabs.setCurrentIndex(0)

    # ======================================================================== Sources
    def _sources_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(_heading("Where videos come from"))
        lay.addWidget(_hint(
            "One per line:\n"
            "  @creator  - that TikTok creator's videos\n"
            "  #hashtag  - TikTok videos with that hashtag\n"
            "  https://… - a TikTok video/profile/collection link, or any link yt-dlp supports "
            "(e.g. a YouTube Shorts channel or playlist)\n"
            "The algorithm then ranks everything these return."))
        self.sources_edit = QPlainTextEdit("\n".join(self.app.sources))
        self.sources_edit.setPlaceholderText("@khaby.lame\n#cooking\nhttps://www.tiktok.com/@nasa")
        self.sources_edit.setMinimumHeight(150)
        lay.addWidget(self.sources_edit)

        lay.addWidget(_heading("Videos on this computer"))
        row = QHBoxLayout()
        self.folder_edit = QLineEdit(self.app.local_folder)
        self.folder_edit.setPlaceholderText("A folder of .mp4/.webm files (optional)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_folder)
        row.addWidget(self.folder_edit, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        lay.addWidget(_hint("Optional next to each file: name.info.json (yt-dlp metadata) and "
                            "name.comments.json. Local files are never deleted."))
        demo = QPushButton("Make offline demo videos (needs ffmpeg)")
        demo.clicked.connect(self._make_demo)
        lay.addWidget(demo)
        lay.addStretch(1)
        return w

    def _browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Video folder", self.folder_edit.text())
        if path:
            self.folder_edit.setText(path)

    def _make_demo(self) -> None:
        from ..demo import make_demo

        target = data_dir() / "demo_videos"
        self.setCursor(Qt.WaitCursor)
        try:
            make_demo(target)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Demo failed", str(exc))
            return
        finally:
            self.unsetCursor()
        self.folder_edit.setText(str(target))
        QMessageBox.information(self, "Demo ready", f"Created demo videos in {target}. Press Save to watch them.")

    # ======================================================================== Storage
    def _storage_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(_heading("Storage"))
        lay.addWidget(_hint(
            f"Downloaded videos are deleted once you scroll past them - only the previous, current and the next "
            f"few stay on disk. Up to {COMMENT_LIMIT} comments are downloaded for the video on screen and deleted "
            f"when you swipe to the next one. Everything is wiped when you close MiniTok."))
        form = QFormLayout()
        self.prefetch = QSpinBox()
        self.prefetch.setRange(1, 5)
        self.prefetch.setValue(int(self.app.prefetch))
        form.addRow("Download ahead", self.prefetch)
        self.quality = QComboBox()
        for h in (360, 480, 720, 1080):
            self.quality.addItem(f"{h}p", h)
        self.quality.setCurrentIndex(max(0, self.quality.findData(int(self.app.max_height))))
        form.addRow("Max quality", self.quality)
        self.cookies = QComboBox()
        for b in BROWSERS:
            self.cookies.addItem(b or "(none)", b)
        self.cookies.setCurrentIndex(max(0, self.cookies.findData(self.app.cookies_from_browser)))
        form.addRow("Use cookies from", self.cookies)
        lay.addLayout(form)
        lay.addWidget(_hint("Cookies: only if TikTok refuses downloads without being logged in. yt-dlp reads them "
                            "from that browser on this computer; they are not stored by MiniTok."))
        size = self.cache.size_bytes() / (1024 * 1024)
        lay.addWidget(QLabel(f"Cache in use right now: {size:.1f} MB"))
        open_btn = QPushButton("Open MiniTok data folder")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir()))))
        lay.addWidget(open_btn, 0, Qt.AlignLeft)
        lay.addStretch(1)
        return w

    # ======================================================================== save
    def _accept(self) -> None:
        self._read_algo_widgets()
        if self.algo.preset == "formula" and not self._check_formula():
            self.tabs.setCurrentIndex(0)
            return
        self._stop.set()
        self.accept()

    def reject(self) -> None:
        self._stop.set()
        super().reject()

    def apply_to(self, config: AppConfig) -> bool:
        """Copy the edited settings into ``config``. Returns True if the sources changed."""
        config.algorithm = self.algo
        config.sources = [ln.strip() for ln in self.sources_edit.toPlainText().splitlines() if ln.strip()]
        config.local_folder = self.folder_edit.text().strip()
        config.prefetch = self.prefetch.value()
        config.max_height = int(self.quality.currentData())
        config.cookies_from_browser = self.cookies.currentData() or ""
        return (config.sources, config.local_folder) != self.original_sources
