# 客户列表项组件模块 - 中间面板
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox
from PyQt6.QtGui import QFont

from qfluentwidgets import StrongBodyLabel, CaptionLabel, PushButton
from utils.logger_loguru import get_logger

from .threads import SyncMessagesThread

logger = get_logger("CustomerListItem")


class CustomerListItem(QWidget):
    """客户列表项 - 中间面板"""

    customer_clicked = pyqtSignal(dict)  # 点击客户信号
    # 同步成功后回传，供协调器刷新右侧聊天面板（唯一够不着的跨组件副作用）
    sync_finished = pyqtSignal(dict, dict, dict, list)  # (account_data, customer_data, user_info, messages)

    def __init__(self, customer_data: dict, account_data: dict = None, parent=None):
        super().__init__(parent)
        self.customer_data = customer_data
        self.account_data = account_data or {}
        self.from_uid = customer_data.get("from_uid", "")
        self._sync_thread = None
        self.nickname = customer_data.get("nickname", "未知客户")
        self.last_message = customer_data.get("last_message", "")
        self.last_time = customer_data.get("last_time", "")
        self.unread_count = 0
        self.setupUI()

    def setupUI(self):
        """设置UI"""
        self.setFixedHeight(70)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            CustomerListItem {
                background-color: #ffffff;
                border-bottom: 1px solid #f0f0f0;
            }
            CustomerListItem:hover {
                background-color: #f5f5f5;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(12)

        # 头像
        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(45, 45)
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setStyleSheet("""
            QLabel {
                background-color: #07c160;
                color: white;
                border-radius: 22px;
                font-size: 18px;
                font-weight: bold;
                font-family: "Microsoft YaHei";
            }
        """)
        # 显示昵称首字
        avatar_char = self.nickname[0] if self.nickname else "?"
        self.avatar_label.setText(avatar_char)

        # 信息区域
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(4)

        # 第一行：昵称 + 时间
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        nickname_label = StrongBodyLabel(self.nickname)
        nickname_label.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        nickname_label.setStyleSheet("color: #2c3e50;")

        top_layout.addWidget(nickname_label)
        top_layout.addStretch()

        if self.last_time:
            time_label = CaptionLabel(self.last_time)
            time_label.setStyleSheet("color: #95a5a6; font-size: 11px;")
            top_layout.addWidget(time_label)

        # 第二行：最后消息
        msg_label = CaptionLabel(self.last_message or "暂无消息")
        msg_label.setStyleSheet("color: #7f8c8d;")
        msg_label.setMaximumWidth(180)

        info_layout.addWidget(top_row)
        info_layout.addWidget(msg_label)

        layout.addWidget(self.avatar_label)
        layout.addWidget(info_widget, 1)

        # 消息同步按钮
        self.sync_btn = PushButton("同步")
        self.sync_btn.setFixedSize(70, 28)
        self.sync_btn.setStyleSheet("""
            PushButton {
                background-color: #409eff;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
            }
            PushButton:hover {
                background-color: #66b1ff;
            }
            PushButton:pressed {
                background-color: #3a8ee6;
            }
        """)
        self.sync_btn.clicked.connect(self._on_sync_clicked)
        layout.addWidget(self.sync_btn)

    def _on_sync_clicked(self):
        """点击同步按钮：卡片内直接启动同步线程（逻辑内聚）"""
        if not self.account_data:
            QMessageBox.warning(self, "同步失败", "缺少账号信息")
            return

        # 显示同步中状态
        self.sync_btn.setText("同步中...")
        self.sync_btn.setEnabled(False)

        # 启动后台线程同步消息（每张卡片各持一个线程引用，避免相互覆盖）
        self._sync_thread = SyncMessagesThread(self.account_data, self.from_uid)
        self._sync_thread.sync_success.connect(self._on_sync_success)
        self._sync_thread.sync_failed.connect(self._on_sync_failed)
        self._sync_thread.start()

    def _on_sync_success(self, user_info: dict, messages: list):
        """同步成功：恢复按钮 + 弹窗，并回传信号请协调器刷新聊天面板"""
        self.sync_btn.setText("同步")
        self.sync_btn.setEnabled(True)

        logger.info(f"同步成功: {len(messages)} 条消息")
        QMessageBox.information(self, "同步成功", f"已同步 {len(messages)} 条消息")

        # 仅“刷新右侧聊天面板”这一跨组件副作用回传给协调器
        self.sync_finished.emit(self.account_data, self.customer_data, user_info, messages)

    def _on_sync_failed(self, error_msg: str):
        """同步失败：恢复按钮 + 弹窗（纯自身，不回传）"""
        self.sync_btn.setText("同步")
        self.sync_btn.setEnabled(True)

        logger.error(f"同步失败: {error_msg}")
        QMessageBox.warning(self, "同步失败", error_msg)

    def update_last_message(self, content: str, timestamp: float = None):
        """更新最后一条消息"""
        # 延迟导入，避免与 chat_panel 形成模块级循环导入（与 shop_list_item 一致）
        from .chat_panel import filter_message_content
        clean_content = filter_message_content(content)
        self.last_message = clean_content[:25] + "..." if len(clean_content) > 25 else clean_content
        if timestamp:
            self.last_time = datetime.fromtimestamp(timestamp).strftime("%H:%M")

    def mousePressEvent(self, event):
        """鼠标点击事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.customer_clicked.emit(self.customer_data)
        super().mousePressEvent(event)


__all__ = ['CustomerListItem']
