"""可选 Qt 桌面导入插件；CLI 不导入此模块。"""
from __future__ import annotations

import ipaddress
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QProgressBar, QMessageBox, QCheckBox, QAbstractItemView, QTabWidget, QInputDialog,
    QDialog, QFrame,
)

from . import config, experiment as ex, session
from .file_tags import normalize_tags
from .importer import scan_sources, import_items, target_relative, set_file_tags
from .phone_import import PhoneReceiver, pending_uploads, clean_upload
from .util import human_size, open_path

STYLE = """
QWidget { color: #243b34; font-size: 13px; }
QMainWindow, QDialog { background: #f3f6f3; }
QLabel#eyebrow { color: #527469; font-size: 11px; font-weight: 600; }
QLabel#title { font-size: 28px; font-weight: 650; color: #173f32; }
QLabel#muted { color: #698076; }
QFrame#drop { background: #eaf1eb; border: 1px dashed #9bb8a4; border-radius: 12px; }
QPushButton { background: #fff; border: 1px solid #cbd9d0; border-radius: 7px; padding: 9px 16px; }
QPushButton:hover { background: #e7f0e9; border-color: #75a58c; }
QPushButton#primary { background: #246d50; color: white; border: 1px solid #246d50; font-weight: 600; }
QPushButton#primary:hover { background: #185a3f; }
QPushButton:disabled { background: #e7ebe7; color: #8b9990; border-color: #dbe1dc; }
QPushButton#primary:disabled { background: #e7ebe7; color: #8b9990; border-color: #dbe1dc; }
QLineEdit, QComboBox { background: white; border: 1px solid #cbd9d0; border-radius: 6px; padding: 8px; selection-background-color: #c6e1cd; }
QComboBox::drop-down { border: none; width: 24px; }
QTableWidget { background: white; border: 1px solid #d8e1d9; border-radius: 8px; gridline-color: #ecf0eb; selection-background-color: #dcecdf; selection-color: #173f32; }
QHeaderView::section { background: #eef3ee; border: none; border-bottom: 1px solid #d8e1d9; padding: 10px; text-align: left; color: #597063; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; padding: 12px 18px; color: #698076; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #246d50; border-bottom: 2px solid #246d50; font-weight: 600; }
QProgressBar { border: none; background: #dfe8df; height: 6px; border-radius: 3px; text-align: center; }
QProgressBar::chunk { background: #3c8b60; border-radius: 3px; }
QCheckBox { spacing: 7px; }
QToolTip { background: #173f32; color: white; padding: 7px; border: none; }
"""


class Job(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)
    item_done = Signal(str, str, str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel = threading.Event()

    def run(self):
        try:
            self.result.emit(self.function(self))
        except Exception as exc:
            self.failed.emit(str(exc))


class DropFrame(QFrame):
    paths_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        label = QLabel("拖入文件或文件夹，或使用上方按钮添加")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        small = QLabel("图片 · 手机视频 · CSV / Excel · 仪器原始文件　/　保留源文件与文件夹层级")
        small.setObjectName("muted")
        small.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(small)
        self.setMinimumHeight(86)

    def dragEnterEvent(self, event):
        if self.isEnabled() and event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.isEnabled():
            self.paths_dropped.emit([Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()])
            event.acceptProposedAction()


def table(headers):
    view = QTableWidget(0, len(headers))
    view.setHorizontalHeaderLabels(headers)
    view.verticalHeader().setVisible(False)
    view.verticalHeader().setDefaultSectionSize(42)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    view.setAlternatingRowColors(False)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    return view


def cell(text, *, editable=False):
    item = QTableWidgetItem(str(text))
    if not editable:
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class PhoneDialog(QDialog):
    received = Signal(object)

    def __init__(self, inbox, experiment, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手机传入 · expvault")
        self.setMinimumWidth(430)
        self.receiver = None
        self.inbox, self.experiment = inbox, experiment
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        heading = QLabel("校园网环境：先选择可互通的连接")
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)
        self.mode = QComboBox()
        self.mode.addItems(["校园网：手机 / 电脑热点，或 USB 共享网络（推荐）", "设备可互通的普通局域网"])
        layout.addWidget(self.mode)
        self.network_hint = QLabel()
        self.network_hint.setWordWrap(True)
        self.network_hint.setObjectName("muted")
        layout.addWidget(self.network_hint)
        self.mode.currentIndexChanged.connect(self.update_network_hint)
        self.update_network_hint()
        self.address = QComboBox()
        addresses = []
        for address in QNetworkInterface.allAddresses():
            host = address.toString()
            try:
                ip = ipaddress.ip_address(host)
                if ip.version == 4 and ip.is_private and not ip.is_loopback and not ip.is_link_local:
                    addresses.append(host)
            except ValueError:
                pass
        self.address.addItems(sorted(set(addresses)))
        self.address.setPlaceholderText("未找到局域网 IPv4 地址，请先连接 Wi-Fi")
        layout.addWidget(self.address)
        self.start_button = QPushButton("开启手机传入")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.toggle)
        layout.addWidget(self.start_button)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.qr)
        self.url = QLineEdit()
        self.url.setReadOnly(True)
        self.url.setPlaceholderText("开启后显示配对链接")
        layout.addWidget(self.url)
        self.copy_button = QPushButton("复制配对链接")
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.url.text()))
        layout.addWidget(self.copy_button)
        hint = QLabel("扫码后选择照片、视频或文件，也可以填写文件标签。\n"
                      "手机上传后，回到主窗口核对清单，再点击开始导入。\n\n"
                      "仅在可信局域网使用（HTTP）；链接 2 小时有效。\n"
                      "关闭此窗口即停止接收。Windows 防火墙请允许专用网络。\n"
                      "若多个网卡，请选择热点 / USB 共享网卡的地址。\n"
                      "热点或网线共享需要自行在系统设置中开启。")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.counter = QLabel("尚未接收文件")
        layout.addWidget(self.counter)
        self.count = 0
        self.received.connect(self.count_received)

    def update_network_hint(self):
        self.network_hint.setText(
            "校园网常有设备隔离，同一校园网不保证能传。建议让手机连接电脑热点，"
            "或电脑临时连接手机热点 / USB 共享网络，再重新打开本窗口选择新网卡。"
            "若不能切换网络，请用主窗口「USB 手机 / 相机」导出后导入，无需网络。"
            if self.mode.currentIndex() == 0 else
            "手机和电脑需能直接访问彼此。若扫码打不开，请检查网络隔离、防火墙和所选网卡；"
            "校园网 / 访客 Wi-Fi 请优先使用独立热点或 USB 导出。")

    def count_received(self, _item):
        self.count += 1
        self.counter.setText(f"已接收 {self.count} 个文件 · 等待桌面端导入")

    def toggle(self):
        if self.receiver:
            self.receiver.close()
            self.receiver = None
            self.start_button.setText("开启手机传入")
            self.address.setEnabled(True)
            self.url.clear()
            self.qr.clear()
            return
        host = self.address.currentText()
        if not host:
            QMessageBox.information(self, "尚未连接", "请先连接 Wi-Fi，再重新打开手机传入窗口。")
            return
        try:
            import qrcode
            self.receiver = PhoneReceiver(self.inbox, self.experiment, host=host, on_received=self.received.emit).start()
            self.url.setText(self.receiver.url)
            qr = qrcode.QRCode(border=4, box_size=1)
            qr.add_data(self.receiver.url)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            size = len(matrix)
            image = QImage(size, size, QImage.Format.Format_RGB32)
            image.fill(QColor("white"))
            for y, row in enumerate(matrix):
                for x, dark in enumerate(row):
                    if dark:
                        image.setPixelColor(x, y, QColor("#123729"))
            self.qr.setPixmap(QPixmap.fromImage(image).scaled(250, 250, Qt.AspectRatioMode.KeepAspectRatio,
                                                           Qt.TransformationMode.FastTransformation))
            self.start_button.setText("停止接收")
            self.address.setEnabled(False)
        except Exception as exc:
            if self.receiver:
                self.receiver.close()
                self.receiver = None
            QMessageBox.warning(self, "无法开启手机传入", str(exc))

    def done(self, result):
        if self.receiver:
            self.receiver.close()
            self.receiver = None
        super().done(result)


class ImportWindow(QMainWindow):
    def __init__(self, root, exp_id=""):
        super().__init__()
        self.root = Path(root).resolve()
        self.ctx = config.load(self.root)
        self.items = []
        self.job = None
        self.pending_result = None
        self.phone_dialog = None
        self.web_server = None
        self.setWindowTitle("expvault · 实验资料导入")
        self.setAcceptDrops(True)
        self.resize(1140, 810)
        self.setMinimumSize(900, 650)
        body = QWidget()
        self.setCentralWidget(body)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(14)
        brand = QLabel("EXPVAULT  /  IMPORT STUDIO")
        brand.setObjectName("eyebrow")
        outer.addWidget(brand)
        head = QHBoxLayout()
        title = QLabel("实验资料，一次归档")
        title.setObjectName("title")
        head.addWidget(title)
        head.addStretch()
        self.web_button = QPushButton("网页管理")
        self.web_button.clicked.connect(self.open_web)
        head.addWidget(self.web_button)
        self.repository = QPushButton("切换实验库")
        self.repository.clicked.connect(self.choose_repository)
        head.addWidget(self.repository)
        outer.addLayout(head)
        self.root_label = QLabel(str(self.root))
        self.root_label.setObjectName("muted")
        self.root_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.root_label)
        target = QHBoxLayout()
        target.addWidget(QLabel("目标实验"))
        self.experiments = QComboBox()
        self.experiments.setMinimumWidth(350)
        self.experiments.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.experiments.currentIndexChanged.connect(self.target_changed)
        target.addWidget(self.experiments, 1)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(lambda: self.load_experiments(self.exp_id))
        target.addWidget(self.refresh_button)
        self.new_button = QPushButton("新建实验")
        self.new_button.clicked.connect(self.new_experiment)
        target.addWidget(self.new_button)
        self.open_button = QPushButton("打开实验目录")
        self.open_button.clicked.connect(self.open_experiment)
        target.addWidget(self.open_button)
        outer.addLayout(target)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        queue = QWidget()
        layout = QVBoxLayout(queue)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)
        actions = QHBoxLayout()
        self.add_button = QPushButton("＋ 选择文件")
        self.add_button.clicked.connect(self.choose_files)
        actions.addWidget(self.add_button)
        self.folder_button = QPushButton("选择文件夹 / U 盘")
        self.folder_button.clicked.connect(self.choose_folder)
        actions.addWidget(self.folder_button)
        self.phone_button = QPushButton("手机扫码传入")
        self.phone_button.setObjectName("primary")
        self.phone_button.clicked.connect(self.open_phone)
        actions.addWidget(self.phone_button)
        self.usb_button = QPushButton("USB 手机 / 相机")
        self.usb_button.clicked.connect(self.open_usb)
        actions.addWidget(self.usb_button)
        actions.addStretch()
        self.remove_button = QPushButton("移除所选")
        self.remove_button.clicked.connect(self.remove_selected)
        actions.addWidget(self.remove_button)
        self.clear_button = QPushButton("清空清单")
        self.clear_button.clicked.connect(self.clear_queue)
        actions.addWidget(self.clear_button)
        layout.addLayout(actions)
        self.drop = DropFrame()
        self.drop.paths_dropped.connect(self.add_paths)
        layout.addWidget(self.drop)
        tags = QHBoxLayout()
        tags.addWidget(QLabel("批量标签"))
        self.batch_tags = QLineEdit()
        self.batch_tags.setPlaceholderText("例如：样品 A, 退火前, 设备检查（逗号分隔；各文件可单独编辑）")
        tags.addWidget(self.batch_tags, 1)
        self.apply_button = QPushButton("追加到所选 / 全部待导入")
        self.apply_button.clicked.connect(self.apply_tags)
        tags.addWidget(self.apply_button)
        layout.addLayout(tags)
        self.queue = table(["文件 / 勾选导入", "类型", "大小", "文件标签（双击编辑）", "状态"])
        self.queue.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.queue.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.queue.setColumnWidth(4, 180)
        self.queue.itemChanged.connect(self.queue_changed)
        self.queue.cellDoubleClicked.connect(self.preview_item)
        layout.addWidget(self.queue, 1)
        options = QHBoxLayout()
        options.addWidget(QLabel("存放位置"))
        self.role = QComboBox()
        for label, value in [("按类型：图片 / 视频 → 记录；数据 → 原始数据", "auto"), ("原始数据", "raw"),
                             ("实验记录", "note"), ("图表", "figure"), ("处理数据", "processed"), ("其他", "other")]:
            self.role.addItem(label, value)
        self.role.currentIndexChanged.connect(self.refresh_queue)
        options.addWidget(self.role)
        options.addStretch()
        self.deduplicate = QCheckBox("跳过相同内容，并合并标签")
        self.deduplicate.setChecked(True)
        options.addWidget(self.deduplicate)
        layout.addLayout(options)
        self.tabs.addTab(queue, "待导入清单")
        library = QWidget()
        lib_layout = QVBoxLayout(library)
        lib_layout.setContentsMargins(0, 12, 0, 0)
        searchbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("按文件名或文件标签筛选本实验资料…")
        self.search.textChanged.connect(self.filter_library)
        searchbar.addWidget(self.search)
        self.tag_files = QPushButton("修改所选文件标签")
        self.tag_files.clicked.connect(self.edit_library_tags)
        searchbar.addWidget(self.tag_files)
        lib_layout.addLayout(searchbar)
        self.library = table(["实验内文件", "类型", "大小", "文件标签"])
        self.library.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.library.cellDoubleClicked.connect(self.preview_library)
        lib_layout.addWidget(self.library)
        self.tabs.addTab(library, "已导入资料 / 标签检索")
        self.status = QLabel("校园网有设备隔离时，选 USB 导出或独立热点。先选实验，再添加资料与文件标签。")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        outer.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        outer.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.summary = QLabel("0 个待导入文件")
        bottom.addWidget(self.summary)
        bottom.addStretch()
        self.cancel_button = QPushButton("取消当前任务")
        self.cancel_button.clicked.connect(self.cancel_job)
        self.cancel_button.setEnabled(False)
        bottom.addWidget(self.cancel_button)
        self.import_button = QPushButton("开始导入")
        self.import_button.setObjectName("primary")
        self.import_button.clicked.connect(self.start_import)
        bottom.addWidget(self.import_button)
        outer.addLayout(bottom)
        self.load_experiments(exp_id or session.get_current_id(self.ctx))
        recovered = pending_uploads(self.ctx.meta_dir / "inbox")
        if recovered:
            self.add_items(recovered)
            self.status.setText(f"已恢复 {len(recovered)} 个未导入的手机文件，请核对目标实验。")

    @property
    def exp_id(self):
        return self.experiments.currentData() or ""

    def load_experiments(self, preferred=""):
        records = self.ctx.db.list_experiments()
        self.experiments.blockSignals(True)
        self.experiments.clear()
        for rec in records:
            self.experiments.addItem(f"{rec['id']}  ·  {rec['title']}", rec["id"])
        index = self.experiments.findData(preferred)
        self.experiments.setCurrentIndex(max(index, 0) if records else -1)
        self.experiments.blockSignals(False)
        self.target_changed()

    def target_changed(self, *_args):
        self.refresh_library()
        self.refresh_queue()
        self.update_controls()

    def new_experiment(self):
        title, ok = QInputDialog.getText(self, "新建实验", "实验标题")
        if not ok or not title.strip():
            return
        def create(job):
            from filelock import FileLock
            ctx = config.load(self.root)
            try:
                with FileLock(str(ctx.meta_dir / "harness.lock"), timeout=0):
                    return ex.create(ctx, title.strip())
            finally:
                if ctx._db is not None:
                    ctx.db.close()
        self.run_job(create, lambda rec: self.load_experiments(rec["id"]), "正在新建实验…")

    def choose_repository(self):
        if self.items:
            QMessageBox.information(self, "先处理清单", "请先导入或清空当前清单，再切换实验库。手机临时文件会保留。")
            return
        directory = QFileDialog.getExistingDirectory(self, "选择已有实验库（包含 config.json）", str(self.root))
        if not directory:
            return
        try:
            ctx = config.load(Path(directory))
            # 提前打开，失败不替换当前上下文。
            ctx.db.list_experiments()
        except Exception as exc:
            QMessageBox.warning(self, "无法打开实验库", str(exc))
            return
        if self.web_server:
            if self.web_server.service.running_jobs():
                ctx.db.close()
                QMessageBox.information(self, "网页任务仍在执行", "请等待网页中的后台任务完成后再切换实验库。")
                return
            self.web_server.close()
            self.web_server = None
        self.ctx.db.close()
        self.ctx, self.root = ctx, ctx.root
        self.root_label.setText(str(self.root))
        self.load_experiments(session.get_current_id(ctx))
        self.add_items(pending_uploads(ctx.meta_dir / "inbox"))

    def open_experiment(self):
        rec = self.ctx.db.get_experiment(self.exp_id)
        if rec:
            self.safe_open(ex.exp_dir(self.ctx, rec))

    def open_web(self):
        import webbrowser
        from .web import WebServer

        try:
            if self.web_server is None:
                self.web_server = WebServer(self.root, port=0).start()
            url = self.web_server.url
            if self.exp_id:
                url = url.replace("/#", f"/?view=experiment&exp_id={self.exp_id}#")
            webbrowser.open(url)
            self.status.setText("网页管理已打开；保持本窗口开启即可持续使用。网页可管理实验、步骤、日志、文件标签、校验和备份。")
        except Exception as exc:
            QMessageBox.warning(self, "无法打开网页管理", str(exc))

    def choose_files(self):
        names, _filter = QFileDialog.getOpenFileNames(self, "选择图片、视频或实验数据", str(Path.home()), "所有文件 (*)")
        if names:
            self.add_paths([Path(name) for name in names])

    def choose_folder(self):
        name = QFileDialog.getExistingDirectory(self, "选择要导入的文件夹或 U 盘目录", str(Path.home()))
        if name:
            self.add_paths([Path(name)])

    def add_paths(self, paths):
        if self.job:
            return
        exclude = self.ctx.exclude_patterns()
        self.run_job(lambda job: scan_sources(paths, exclude=exclude, cancel=job.cancel), self.scan_complete, "正在扫描文件夹…")

    def scan_complete(self, result):
        items, errors = result
        self.add_items(items)
        self.status.setText(f"添加 {len(items)} 个文件。" + (f" 跳过 / 失败 {len(errors)} 项。" if errors else "请核对目标实验和文件标签。"))
        if errors:
            self.show_details("部分文件未加入", errors)

    def add_items(self, items):
        existing = {item.source for item in self.items}
        for item in items:
            if item.source not in existing:
                self.items.append(item)
                existing.add(item.source)
        self.refresh_queue()
        self.update_controls()

    def refresh_queue(self, *_args):
        if not hasattr(self, "queue"):
            return
        checked = {self.queue.item(row, 0).data(Qt.ItemDataRole.UserRole): self.queue.item(row, 0).checkState()
                   for row in range(self.queue.rowCount())}
        self.queue.blockSignals(True)
        self.queue.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            pending = item.status not in ("已导入", "重复已跳过 · 标签已合并")
            name = cell(item.relative)
            name.setData(Qt.ItemDataRole.UserRole, item.key)
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name.setCheckState(checked.get(item.key, Qt.CheckState.Checked) if pending else Qt.CheckState.Unchecked)
            if not pending:
                name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            try:
                destination = item.destination or target_relative(self.ctx, item, self.role.currentData())
            except ValueError as exc:
                destination = str(exc)
            name.setToolTip(f"来源：{item.origin}\n{item.source}\n目标：{destination}")
            for col, value in enumerate([name, cell(item.kind), cell(human_size(item.size)),
                                         cell(", ".join(item.tags), editable=pending), cell(item.status)]):
                self.queue.setItem(row, col, value)
        self.queue.blockSignals(False)
        self.update_summary()

    def queue_changed(self, item):
        if item.column() == 3:
            model = self.items[item.row()]
            try:
                model.tags = normalize_tags(item.text())
            except ValueError as exc:
                QMessageBox.warning(self, "标签无效", str(exc))
                self.refresh_queue()
        self.update_summary()

    def selected_queue_items(self):
        rows = sorted({index.row() for index in self.queue.selectionModel().selectedRows()})
        return [self.items[row] for row in rows]

    def apply_tags(self):
        try:
            tags = normalize_tags(self.batch_tags.text())
            targets = self.selected_queue_items() or self.items
            for item in targets:
                if item.status not in ("已导入", "重复已跳过 · 标签已合并"):
                    item.tags = normalize_tags(item.tags + tags)
            self.refresh_queue()
        except ValueError as exc:
            QMessageBox.warning(self, "标签无效", str(exc))

    def remove_selected(self):
        keys = {item.key for item in self.selected_queue_items()}
        self.items = [item for item in self.items if item.key not in keys]
        self.refresh_queue()

    def clear_queue(self):
        self.items = []
        self.refresh_queue()
        self.status.setText("清单已清空。未导入的手机临时文件仍保留，下次启动会恢复。")

    def update_summary(self):
        if not hasattr(self, "summary"):
            return
        selected = self.checked_items()
        self.summary.setText(f"{len(selected)} 个待导入文件 · {human_size(sum(i.size for i in selected))}　/　清单共 {len(self.items)} 个")
        self.import_button.setEnabled(bool(selected and self.exp_id) and not self.job and not self.phone_dialog)

    def checked_items(self):
        return [item for row, item in enumerate(self.items)
                if item.status not in ("已导入", "重复已跳过 · 标签已合并")
                and self.queue.item(row, 0).checkState() == Qt.CheckState.Checked]

    def start_import(self):
        # 提交仍在编辑的标签单元格。
        self.import_button.setFocus()
        selected = self.checked_items()
        if not selected or not self.exp_id or self.job:
            return
        root, exp_id, role, dedup = self.root, self.exp_id, self.role.currentData(), self.deduplicate.isChecked()
        def execute(job):
            return import_items(root, exp_id, selected, role=role, deduplicate=dedup, cancel=job.cancel,
                                progress=lambda done, total, message: job.progress.emit(min(99, int(done * 100 / max(total, 1))), message),
                                item_done=job.item_done.emit)
        self.run_job(execute, self.import_complete, "正在导入并校验…")

    def import_complete(self, result):
        completed = set(result["imported"] + result["duplicates"])
        for item in self.items:
            if item.key in completed:
                try:
                    clean_upload(item, self.ctx.meta_dir / "inbox")
                except OSError:
                    pass  # 保留临时副本，后续仍可按内容去重。
        self.refresh_queue()
        self.refresh_library()
        self.progress.setValue(0 if result["cancelled"] else 100)
        self.status.setText(f"{'已取消，已完成文件保留' if result['cancelled'] else '导入完成'}："
                            f"成功 {len(result['imported'])} · 重复 {len(result['duplicates'])} · 失败 {len(result['errors'])}。"
                            "可在「已导入资料」中按文件标签查找。")
        if result["errors"]:
            self.show_details("部分文件导入失败", [r["error"] for r in result["errors"]])

    def run_job(self, function, callback, message):
        if self.job:
            return
        self.status.setText(message)
        self.progress.setRange(0, 0)
        self.pending_result = None
        job = Job(function, self)
        job.result.connect(lambda value: setattr(self, "pending_result", (True, value)))
        job.failed.connect(lambda value: setattr(self, "pending_result", (False, value)))
        job.progress.connect(self.job_progress)
        job.item_done.connect(self.item_finished)
        job.finished.connect(lambda: self.job_finished(job, callback))
        self.job = job
        self.update_controls()
        job.start()

    def job_progress(self, value, message):
        self.progress.setRange(0, 100)
        self.progress.setValue(value)
        self.status.setText(message)

    def item_finished(self, key, status, destination):
        for row, item in enumerate(self.items):
            if item.key == key:
                item.status, item.destination = status, destination
                self.queue.item(row, 4).setText(status)
                break

    def job_finished(self, job, callback):
        outcome = self.pending_result
        self.pending_result = None
        self.job = None
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        job.wait()
        job.result.disconnect()
        job.failed.disconnect()
        job.finished.disconnect()
        job.progress.disconnect()
        job.item_done.disconnect()
        job.deleteLater()
        if outcome:
            ok, value = outcome
            if ok:
                callback(value)
            else:
                self.refresh_queue()
                self.status.setText("任务未完成：" + value)
                QMessageBox.warning(self, "任务未完成", value)
        self.update_controls()

    def cancel_job(self):
        if self.job:
            self.job.cancel.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消；已完成的文件和标签会保留。")

    def update_controls(self):
        if not hasattr(self, "import_button"):
            return
        idle = not self.job and not self.phone_dialog
        for widget in (self.experiments, self.refresh_button, self.new_button, self.repository,
                       self.add_button, self.folder_button, self.phone_button, self.usb_button, self.remove_button,
                       self.clear_button, self.batch_tags, self.apply_button, self.role,
                       self.deduplicate, self.drop, self.queue, self.tag_files):
            widget.setEnabled(idle)
        self.phone_button.setEnabled(idle and bool(self.exp_id))
        self.open_button.setEnabled(bool(self.exp_id))
        self.cancel_button.setEnabled(bool(self.job))
        self.update_summary()

    def open_phone(self):
        if not self.exp_id:
            return
        dialog = PhoneDialog(self.ctx.meta_dir / "inbox", self.experiments.currentText(), self)
        self.phone_dialog = dialog
        dialog.received.connect(lambda item: self.add_items([item]))
        dialog.finished.connect(self.phone_closed)
        self.update_controls()
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.open()

    def open_usb(self):
        box = QMessageBox(self)
        box.setWindowTitle("USB 手机 / 相机 · 无需校园网互通")
        box.setText("先通过 USB 将手机资料导出到本机文件夹，再加入本实验清单。")
        if sys.platform == "darwin":
            box.setInformativeText("连接手机并解锁，iPhone 选择信任电脑。\n"
                                   "点击下方按钮打开系统「图像捕捉」，选择手机与导出目录，保留原片。\n"
                                   "Android 可尝试照片传输（PTP）模式；无法识别时改用热点传入。\n"
                                   "导出完成后点击「选择已导出文件夹」，在清单中打标签并导入。")
        else:
            box.setInformativeText("连接手机并解锁，允许访问照片；Android 选择文件传输模式。\n"
                                   "从「此电脑」中的手机复制文件，或用 Windows「照片」应用的导入功能，保存到本机目录。\n"
                                   "iPhone 可能需先安装 Apple Devices；系统未识别时可改用热点传入。\n"
                                   "导出完成后点击「选择已导出文件夹」，在清单中打标签并导入。")
        system = box.addButton("打开系统导入工具", QMessageBox.ButtonRole.ActionRole)
        choose = box.addButton("选择已导出文件夹", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        # 工具启动后保持说明窗口，方便导出完成后直接选择文件夹。
        def launch_system():
            try:
                open_usb_tool()
            except OSError as exc:
                QMessageBox.warning(self, "无法打开系统工具", str(exc))
        system.clicked.connect(launch_system)
        # QMessageBox 的 ActionRole 会结束 exec；循环保留 USB 操作指引。
        while True:
            box.exec()
            if box.clickedButton() == choose:
                self.choose_folder()
                break
            if box.clickedButton() != system:
                break

    def phone_closed(self, _result):
        dialog, self.phone_dialog = self.phone_dialog, None
        if dialog:
            dialog.deleteLater()
        self.update_controls()
        self.status.setText("手机接收已关闭。请核对清单中的文件和标签，再点击开始导入。")

    def refresh_library(self):
        if not hasattr(self, "library"):
            return
        files = self.ctx.db.files_of(self.exp_id, include_missing=False) if self.exp_id else []
        files = [f for f in files if f["role"] != "meta"]
        self.library.setRowCount(len(files))
        for row, f in enumerate(files):
            for col, value in enumerate([f["relpath"], self._kind(f["relpath"]), human_size(f["size"]), ", ".join(f.get("tags", []))]):
                self.library.setItem(row, col, cell(value))
        self.filter_library()

    @staticmethod
    def _kind(path):
        from .importer import file_kind
        return file_kind(Path(path))

    def filter_library(self):
        keyword = self.search.text().casefold()
        for row in range(self.library.rowCount()):
            text = self.library.item(row, 0).text() + " " + self.library.item(row, 3).text()
            self.library.setRowHidden(row, keyword not in text.casefold())

    def edit_library_tags(self):
        rows = [index.row() for index in self.library.selectionModel().selectedRows() if not self.library.isRowHidden(index.row())]
        if not rows:
            QMessageBox.information(self, "选择文件", "请先选择一个或多个文件。")
            return
        default = self.library.item(rows[0], 3).text() if len(rows) == 1 else ""
        value, ok = QInputDialog.getText(self, "修改文件标签", f"替换 {len(rows)} 个文件的标签（逗号分隔，留空清除）", text=default)
        if ok:
            try:
                tags = normalize_tags(value)
            except ValueError as exc:
                QMessageBox.warning(self, "标签无效", str(exc))
                return
            paths = [self.library.item(row, 0).text() for row in rows]
            root, exp_id = self.root, self.exp_id
            self.run_job(lambda job: set_file_tags(root, exp_id, paths, tags),
                         lambda _: self.tags_saved(), "正在保存文件标签…")

    def tags_saved(self):
        self.refresh_library()
        self.status.setText("文件标签已保存；可按标签筛选，重建索引后仍可恢复。")

    def preview_item(self, row, column):
        if column == 0:
            item = self.items[row]
            path = item.source
            if item.destination:
                rec = self.ctx.db.get_experiment(item.destination_exp_id or self.exp_id)
                path = ex.exp_dir(self.ctx, rec) / item.destination if rec else path
            self.safe_open(path)

    def preview_library(self, row, column):
        if column == 0:
            rec = self.ctx.db.get_experiment(self.exp_id)
            if rec:
                self.safe_open(ex.exp_dir(self.ctx, rec) / self.library.item(row, 0).text())

    def safe_open(self, path):
        try:
            open_path(path)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "无法预览", str(exc))

    def show_details(self, title, details):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(f"共 {len(details)} 项，请展开详情查看。")
        box.setDetailedText("\n".join(details))
        box.exec()

    def closeEvent(self, event):
        if self.job:
            self.cancel_job()
            self.status.setText("正在取消后台任务，完成后可关闭窗口；已导入的文件会保留。")
            event.ignore()
            return
        if self.web_server:
            if self.web_server.service.running_jobs():
                self.status.setText("网页中有后台任务正在执行，请等待完成或在网页中取消导入后再关闭。")
                event.ignore()
                return
            self.web_server.close()
            self.web_server = None
        if self.phone_dialog:
            self.phone_dialog.reject()
        self.ctx.db.close()
        event.accept()

    def resizeEvent(self, event):
        if hasattr(self, "drop"):
            self.drop.setVisible(self.height() >= 740)
        super().resizeEvent(event)

    def dragEnterEvent(self, event):
        if not self.job and not self.phone_dialog and event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.job and not self.phone_dialog:
            self.add_paths([Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()])
            event.acceptProposedAction()


def launch(root, exp_id=""):
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("expvault")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = ImportWindow(root, exp_id)
    window.show()
    return app.exec()


def open_usb_tool():
    """使用系统既有手机导入入口；不要求安装专用 USB Python 驱动。"""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Image Capture"])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer.exe", "shell:MyComputerFolder"])
    else:
        open_path(Path.home())
