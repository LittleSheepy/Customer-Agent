# 聊天面板模块 - 仿微信三栏界面
import json
import re
import time
from datetime import datetime
from typing import Optional, Dict, List

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QScrollArea, QFrame, QListWidget, QListWidgetItem, QSizePolicy,
    QMessageBox,
)
from PyQt6.QtGui import QFont, QPixmap, QPainter, QPainterPath, QColor

from qfluentwidgets import (SubtitleLabel, CaptionLabel, BodyLabel,
                            StrongBodyLabel, PushButton,
                            FluentIcon as FIF)
from utils.logger_loguru import get_logger

logger = get_logger("ChatPanel")


def filter_message_content(content: str) -> str:
    """过滤消息内容，去掉 uid、user_id 等非消息内容"""
    if not content:
        return ""

    # 尝试解析 JSON
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            # 提取纯文本内容
            if "text" in data:
                return data["text"]
            elif "content" in data:
                return str(data["content"])
            elif "name" in data:
                return data["name"]  # 商品名称等
            # 返回空字符串而不是 JSON 结构
            return "[消息]"
    except (json.JSONDecodeError, TypeError):
        pass

    # 去掉常见的 uid 模式
    content = re.sub(r'\buid[:\s]*[a-zA-Z0-9_]+\b', '', content, flags=re.IGNORECASE)
    content = re.sub(r'\buser_id[:\s]*[a-zA-Z0-9_]+\b', '', content, flags=re.IGNORECASE)
    content = re.sub(r'\bfrom_uid[:\s]*[a-zA-Z0-9_]+\b', '', content, flags=re.IGNORECASE)

    # 去掉多余的空白
    content = re.sub(r'\s+', ' ', content).strip()

    return content if content else "[消息]"


class ChatBubble(QWidget):
    """聊天气泡组件 - 客户在左，客服在右"""

    def __init__(self, content: str, is_self: bool = False, nickname: str = "",
                 timestamp: float = None, parent=None):
        super().__init__(parent)
        self.content = filter_message_content(content)  # 过滤消息内容
        self.is_self = is_self  # True = 客服（右边），False = 客户（左边）
        self.nickname = nickname
        self.timestamp = timestamp
        self.setupUI()

    def setupUI(self):
        """设置UI"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        # 消息内容区域
        content_widget = QWidget()
        content_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)

        # 昵称和时间行
        if self.nickname:
            info_row = QWidget()
            info_layout = QHBoxLayout(info_row)
            info_layout.setContentsMargins(0, 0, 0, 0)

            if self.is_self:
                # 客服：昵称和时间靠右
                info_layout.addStretch()

            nickname_label = CaptionLabel(self.nickname)
            nickname_label.setStyleSheet("color: #7f8c8d; font-size: 11px;")
            info_layout.addWidget(nickname_label)

            if self.timestamp:
                time_str = datetime.fromtimestamp(self.timestamp).strftime("%H:%M")
                time_label = CaptionLabel(time_str)
                time_label.setStyleSheet("color: #95a5a6; font-size: 11px;")
                info_layout.addWidget(time_label)

            if not self.is_self:
                # 客户：昵称和时间靠左
                info_layout.addStretch()

            content_layout.addWidget(info_row)

        # 气泡
        bubble = QLabel(self.content)
        bubble.setWordWrap(True)
        bubble.setTextFormat(Qt.TextFormat.PlainText)
        bubble.setMaximumWidth(600)  # 增加最大宽度
        bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)  # 允许水平扩展

        if self.is_self:
            # 客服消息 - 绿色气泡
            bubble.setStyleSheet("""
                QLabel {
                    background-color: #95ec69;
                    color: #000000;
                    padding: 10px 14px;
                    border-radius: 8px;
                    font-size: 14px;
                    font-family: "Microsoft YaHei";
                }
            """)
        else:
            # 客户消息 - 白色气泡
            bubble.setStyleSheet("""
                QLabel {
                    background-color: #ffffff;
                    color: #000000;
                    padding: 10px 14px;
                    border-radius: 8px;
                    font-size: 14px;
                    font-family: "Microsoft YaHei";
                    border: 1px solid #e8e8e8;
                }
            """)

        content_layout.addWidget(bubble)

        # 布局：客户消息靠左，客服消息靠右
        if self.is_self:
            # 客服消息：[stretch][content] -> 靠右
            layout.addStretch()
            layout.addWidget(content_widget)
        else:
            # 客户消息：[content][stretch] -> 靠左
            layout.addWidget(content_widget)
            layout.addStretch()


class ChatPanel(QWidget):
    """聊天面板 - 右侧面板"""

    def __init__(self, repo=None, parent=None):
        super().__init__(parent)
        self._repo = repo  # 聊天消息数据访问层（外部注入，用于发送后落库）
        self.current_account = None  # 当前店铺
        self.current_customer = None  # 当前客户
        # 消息存储: shop_key -> from_uid -> [messages]
        self.messages: Dict[str, Dict[str, List[dict]]] = {}
        # 客户信息: shop_key -> from_uid -> customer_data
        self.customers: Dict[str, Dict[str, dict]] = {}
        self.setupUI()

    def setupUI(self):
        """设置UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 聊天标题栏
        self.title_label = SubtitleLabel("请选择店铺和客户查看聊天")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet("padding: 15px; color: #7f8c8d;")

        # 消息列表区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: #f5f5f5;
            }
        """)

        self.messages_container = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setContentsMargins(10, 10, 10, 10)
        self.messages_layout.setSpacing(5)
        self.messages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.messages_container.setStyleSheet("background-color: #f5f5f5;")

        self.scroll_area.setWidget(self.messages_container)

        # 输入区域
        input_widget = QWidget()
        input_widget.setStyleSheet("""
            QWidget {
                background-color: #ffffff;
                border-top: 1px solid #e8e8e8;
            }
        """)
        input_layout = QHBoxLayout(input_widget)
        input_layout.setContentsMargins(10, 10, 10, 10)
        input_layout.setSpacing(10)

        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("输入消息...")
        self.input_text.setMaximumHeight(80)
        self.input_text.setMinimumHeight(50)
        self.input_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #e0e0e0;
                border-radius: 5px;
                padding: 8px;
                font-size: 14px;
                font-family: "Microsoft YaHei";
            }
        """)

        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedSize(80, 40)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #07c160;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
                font-family: "Microsoft YaHei";
            }
            QPushButton:hover {
                background-color: #06ad56;
            }
            QPushButton:pressed {
                background-color: #059a4c;
            }
            QPushButton:disabled {
                background-color: #c0c0c0;
            }
        """)
        self.send_btn.setEnabled(False)

        input_layout.addWidget(self.input_text, 1)
        input_layout.addWidget(self.send_btn)

        layout.addWidget(self.title_label)
        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(input_widget)

        # 连接信号
        self.send_btn.clicked.connect(self._on_send_clicked)
        self.input_text.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self):
        """文本变化时更新发送按钮状态"""
        has_text = bool(self.input_text.toPlainText().strip())
        self.send_btn.setEnabled(has_text and self.current_customer is not None)

    def _on_send_clicked(self):
        """发送按钮点击：直接在面板内完成发送（逻辑内聚，不再上抛父窗口）"""
        if not self.current_account or not self.current_customer:
            return

        content = self.input_text.toPlainText().strip()
        if not content:
            return

        from_uid = self.current_customer.get("from_uid", "")
        self._send_message(self.current_account, content, from_uid)

        # 清空输入框
        self.input_text.clear()

    def _send_message(self, account_data: dict, content: str, from_uid: str):
        """发送消息：调用 PDD 接口 + 刷新气泡 + 写内存缓存 + 落库"""
        try:
            from Channel.pinduoduo.utils.API.send_message import SendMessage

            if not from_uid:
                QMessageBox.warning(self, "发送失败", "无法确定消息接收者")
                return

            shop_id = account_data.get("shop_id", "")

            # 发送消息
            sender = SendMessage(
                shop_id=shop_id,
                user_id=account_data.get("user_id", "")
            )
            result = sender.send_text(from_uid, content)

            if result:
                ts = int(time.time())

                # 添加发送的消息到聊天面板（自身方法）
                self.add_message(
                    account_data=account_data,
                    content=content,
                    is_self=True,
                    nickname="客服",
                    timestamp=ts,
                    from_uid=from_uid
                )

                # 保存到内存缓存
                from Channel.pinduoduo.cookie_cache import message_cache
                cached = message_cache.get(shop_id, from_uid)
                if cached is not None:
                    msg_data = {
                        "content": content,
                        "direction": True,  # 客服发送
                        "csname": "客服",
                        "ts": ts
                    }
                    cached.append(msg_data)
                    message_cache.set(shop_id, from_uid, cached)

                # 保存到数据库（repo 由外部注入）
                if self._repo is not None:
                    self._repo.append_message(
                        shop_id, from_uid, content, True, "客服", ts,
                        account_data.get("user_id", ""),
                    )

                logger.info(f"消息发送成功: {account_data.get('shop_name', '')} -> {from_uid}")
            else:
                QMessageBox.warning(self, "发送失败", "消息发送失败，请检查网络连接")

        except Exception as e:
            logger.error(f"发送消息失败: error_type={type(e).__name__}")
            QMessageBox.critical(self, "错误", f"发送消息失败: {str(e)}")

    def select_shop(self, account_data: dict):
        """选择店铺"""
        self.current_account = account_data
        self.current_customer = None  # 重置客户选择
        self._update_title()
        self._clear_messages()
        self.send_btn.setEnabled(False)

    def select_customer(self, customer_data: dict):
        """选择客户"""
        self.current_customer = customer_data
        self._update_title()
        self.send_btn.setEnabled(bool(self.input_text.toPlainText().strip()))

        # 加载该客户与当前店铺的消息
        self._load_messages()

    def _get_current_from_uid(self) -> str:
        """获取当前选中客户的 from_uid"""
        if self.current_customer:
            return self.current_customer.get("from_uid", "")
        return ""

    def _update_title(self):
        """更新聊天标题"""
        if self.current_account and self.current_customer:
            shop_name = self.current_account.get("shop_name", "未知店铺")
            customer_name = self.current_customer.get("nickname", "未知客户")
            self.title_label.setText(f"【{shop_name}】与【{customer_name}】的聊天")
            self.title_label.setStyleSheet("padding: 15px; color: #2c3e50; font-weight: bold;")
        elif self.current_account:
            shop_name = self.current_account.get("shop_name", "未知店铺")
            self.title_label.setText(f"【{shop_name}】- 请选择客户")
            self.title_label.setStyleSheet("padding: 15px; color: #7f8c8d;")
        else:
            self.title_label.setText("请选择店铺和客户查看聊天")
            self.title_label.setStyleSheet("padding: 15px; color: #7f8c8d;")

    def _get_shop_key(self, account_data: dict) -> str:
        """获取店铺唯一标识"""
        return "_".join(
            str(account_data.get(key, ""))
            for key in ("channel_name", "shop_id", "user_id")
        )

    def add_customer(self, account_data: dict, from_uid: str, nickname: str):
        """添加客户到客户列表"""
        shop_key = self._get_shop_key(account_data)

        if shop_key not in self.customers:
            self.customers[shop_key] = {}

        if from_uid not in self.customers[shop_key]:
            self.customers[shop_key][from_uid] = {
                "from_uid": from_uid,
                "nickname": nickname or f"客户_{from_uid[-6:]}",
                "shop_key": shop_key,
                "account_data": account_data
            }

        return self.customers[shop_key][from_uid]

    def get_customers(self, account_data: dict) -> List[dict]:
        """获取店铺的所有客户"""
        shop_key = self._get_shop_key(account_data)
        return list(self.customers.get(shop_key, {}).values())

    def add_message(self, account_data: dict, content: str, is_self: bool = False,
                    nickname: str = "", timestamp: float = None, from_uid: str = "",
                    _scroll: bool = True):
        """添加消息
        
        Args:
            _scroll: 是否自动滚动到底部，批量添加时设为 False
        """
        shop_key = self._get_shop_key(account_data)

        if shop_key not in self.messages:
            self.messages[shop_key] = {}

        # 如果没有 from_uid，使用默认值
        if not from_uid:
            from_uid = "unknown"

        if from_uid not in self.messages[shop_key]:
            self.messages[shop_key][from_uid] = []

        msg_data = {
            "content": content,
            "is_self": is_self,
            "nickname": nickname or ("客服" if is_self else f"客户_{from_uid[-6:]}"),
            "timestamp": timestamp or datetime.now().timestamp(),
            "from_uid": from_uid
        }
        self.messages[shop_key][from_uid].append(msg_data)

        # 如果当前显示的是这个店铺和客户的消息，则添加气泡
        if (self.current_account and self.current_customer and
                self._get_shop_key(self.current_account) == shop_key and
                self.current_customer.get("from_uid") == from_uid):
            self._add_message_bubble(msg_data, scroll_to_bottom=_scroll)

    def _load_messages(self):
        """加载消息"""
        self._clear_messages()

        if not self.current_account or not self.current_customer:
            return

        shop_key = self._get_shop_key(self.current_account)
        from_uid = self.current_customer.get("from_uid", "")

        messages = self.messages.get(shop_key, {}).get(from_uid, [])

        for msg in messages:
            self._add_message_bubble(msg)

    def _add_message_bubble(self, msg_data: dict, scroll_to_bottom: bool = True):
        """添加消息气泡"""
        bubble = ChatBubble(
            content=msg_data["content"],
            is_self=msg_data.get("is_self", False),
            nickname=msg_data.get("nickname", ""),
            timestamp=msg_data.get("timestamp")
        )
        self.messages_layout.addWidget(bubble)

        # 只有在单条消息添加时才自动滚动
        if scroll_to_bottom:
            QTimer.singleShot(100, lambda: self.scroll_area.verticalScrollBar().setValue(
                self.scroll_area.verticalScrollBar().maximum()
            ))

    def scroll_to_bottom(self):
        """滚动到最新消息"""
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()  # 确保布局完成
        scrollbar = self.scroll_area.verticalScrollBar()
        max_val = scrollbar.maximum()
        if max_val > 0:
            scrollbar.setValue(max_val)

    def _clear_messages(self):
        """清空消息"""
        while self.messages_layout.count():
            child = self.messages_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()


__all__ = ['ChatBubble', 'ChatPanel', 'filter_message_content']
