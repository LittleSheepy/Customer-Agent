# 店铺列表项组件模块
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtWidgets import QLabel, QWidget, QHBoxLayout, QVBoxLayout, QComboBox, QMessageBox
from PyQt6.QtGui import QFont, QPixmap

from qfluentwidgets import (CardWidget, StrongBodyLabel, CaptionLabel, BodyLabel,
                            PrimaryPushButton, InfoBadge, FluentIcon as FIF)
from utils.logger_loguru import get_logger
from .manager import auto_reply_manager
from .threads import LogoLoaderThread, SetStatusThread

logger = get_logger("ShopListItem")


class ShopListItem(CardWidget):
    """店铺列表项 - 左侧面板"""

    shop_clicked = pyqtSignal(dict)  # 点击店铺信号，传递 account_data
    # 自动回复后台线程收到新消息 → 转交协调器做跨三栏更新（跨线程队列连接，线程安全）
    new_message = pyqtSignal(dict, str, bool, str, float, str)  # (account_data, content, is_self, nickname, timestamp, from_uid)
    stats_changed = pyqtSignal()  # 请求父窗口刷新顶部统计

    def __init__(self, account_data: dict, parent=None):
        super().__init__(parent)
        self.account_data = account_data
        self.shop_id = account_data.get("shop_id", "")
        self.shop_name = account_data.get("shop_name", "")
        self.shop_logo = account_data.get("shop_logo")
        self.account_name = account_data.get("username", "")
        self.platform = account_data.get("channel_name", "")
        self.status = self._getStatusText(account_data.get("status", 0))
        self.auto_reply_status = False  # 自动回复状态
        self.last_message = ""
        self.last_message_time = ""
        self.setupUI()
        self.connectSignals()
        self.loadLogo()

    @staticmethod
    def _getStatusText(status_code: int) -> str:
        """将状态码转换为文本"""
        status_map = {0: "休息", 1: "在线", 2: "忙碌", 3: "离线", None: "未验证"}
        return status_map.get(status_code, "未知")

    @staticmethod
    def _comboIndexToStatus(index: int) -> int:
        """下拉框索引转状态码: 0→1(在线), 1→2(忙碌), 2→3(离线)"""
        return {0: 1, 1: 2, 2: 3}.get(index, 3)

    def setupUI(self):
        """设置UI"""
        self.setFixedHeight(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(12)

        # Logo
        logo_widget = self.createLogoWidget()

        # 中间：信息区域
        info_widget = self.createInfoWidget()

        # 右侧：操作区域
        self.action_widget = self.createActionWidget()

        layout.addWidget(logo_widget)
        layout.addWidget(info_widget, 1)
        layout.addWidget(self.action_widget, 0)

    def createLogoWidget(self):
        """创建Logo显示区域"""
        self.logo_label = QLabel()
        self.logo_label.setFixedSize(65, 65)
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setStyleSheet(
            "border-radius: 32px; border: 1px solid #e0e0e0; background-color: #f5f5f5;"
        )
        self.logo_label.setText("加载中...")
        return self.logo_label

    def loadLogo(self):
        """异步加载Logo"""
        if self.shop_logo:
            def _start():
                self.logo_loader_thread = LogoLoaderThread(self.shop_logo)
                self.logo_loader_thread.logo_loaded.connect(self.setLogo)
                self.logo_loader_thread.start()
            QTimer.singleShot(200, _start)
        else:
            self.logo_label.setText("无Logo")

    def setLogo(self, pixmap: QPixmap):
        """设置Logo"""
        if not pixmap.isNull():
            self.logo_label.setPixmap(pixmap)
        else:
            self.logo_label.setText("加载失败")

    def createInfoWidget(self):
        """创建信息显示区域"""
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setSpacing(6)
        info_layout.setContentsMargins(0, 0, 0, 0)

        # 第一行：店铺名称 + 平台标签
        first_row = QWidget()
        first_row_layout = QHBoxLayout(first_row)
        first_row_layout.setContentsMargins(0, 0, 0, 0)
        first_row_layout.setSpacing(10)

        shop_name_label = StrongBodyLabel(self.shop_name)
        shop_name_label.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        shop_name_label.setStyleSheet("color: #2c3e50;")

        platform_badge = InfoBadge.info(self.platform, self)
        platform_badge.setFont(QFont("Microsoft YaHei", 9))

        first_row_layout.addWidget(shop_name_label)
        first_row_layout.addWidget(platform_badge)
        first_row_layout.addStretch()

        # 第二行：店铺ID
        second_row = self.createInfoRow("店铺ID:", self.shop_id)

        # 第三行：账号名称
        third_row = self.createInfoRow("账号:", self.account_name)

        # 第四行：最后消息
        self.msg_row = self.createInfoRow("消息:", self.last_message or "暂无消息")
        self.msg_label = self.msg_row.findChild(QLabel)  # 保存消息标签引用

        info_layout.addWidget(first_row)
        info_layout.addWidget(second_row)
        info_layout.addWidget(third_row)
        info_layout.addWidget(self.msg_row)
        info_layout.addStretch()

        return info_widget

    def createInfoRow(self, label_text: str, value_text: str):
        """创建信息行"""
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        label = CaptionLabel(label_text)
        label.setStyleSheet("color: #7f8c8d; font-weight: 500;")
        label.setFixedWidth(60)

        value = BodyLabel(value_text)
        value.setStyleSheet("color: #34495e;")

        row_layout.addWidget(label)
        row_layout.addWidget(value)
        row_layout.addStretch()

        return row_widget

    def createActionWidget(self):
        """创建操作区域 - 状态下拉框 + 自动回复按钮（垂直排列）"""
        action_widget = QWidget()
        action_layout = QVBoxLayout(action_widget)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        action_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 状态标签
        self.status_badge = self.createStatusBadge()

        # 状态下拉框：在线(1)、忙碌(2)、离线(3)
        self.status_combo = QComboBox()
        self.status_combo.addItems(["在线", "忙碌", "离线"])
        self.status_combo.setFixedSize(100, 30)
        self.status_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #dcdfe6;
                border-radius: 4px;
                padding: 4px 8px;
                background: #ffffff;
                color: #303133;
                font-size: 13px;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox QAbstractItemView {
                border: 1px solid #dcdfe6;
                background: #ffffff;
                color: #303133;
                selection-background-color: #ecf5ff;
                selection-color: #409eff;
            }
        """)
        # 设置当前状态
        current_status = self.account_data.get("status", 0)
        if current_status == 1:
            self.status_combo.setCurrentIndex(0)  # 在线
        elif current_status == 2:
            self.status_combo.setCurrentIndex(1)  # 忙碌
        elif current_status == 3:
            self.status_combo.setCurrentIndex(2)  # 离线

        # 自动回复按钮（下拉框下方）
        self.auto_reply_btn = PrimaryPushButton("开始回复")
        self.auto_reply_btn.setIcon(FIF.ROBOT)
        self.auto_reply_btn.setFixedSize(110, 32)

        action_layout.addWidget(self.status_badge, 0, Qt.AlignmentFlag.AlignRight)
        action_layout.addWidget(self.status_combo)
        action_layout.addWidget(self.auto_reply_btn)
        action_layout.addStretch()

        return action_widget

    def createStatusBadge(self):
        """创建状态标签"""
        if self.status == "在线":
            status_badge = InfoBadge.success("● 在线", self)
        elif self.status == "忙碌":
            status_badge = InfoBadge.warning("● 忙碌", self)
        elif self.status == "离线":
            status_badge = InfoBadge.error("● 离线", self)
        elif self.status == "未验证":
            status_badge = InfoBadge.warning("● 未验证", self)
        elif self.status == "休息":
            status_badge = InfoBadge.info("● 休息", self)
        else:
            status_badge = InfoBadge.info(f"● {self.status}", self)

        status_badge.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.Bold))
        return status_badge

    def connectSignals(self):
        """连接信号"""
        self.status_combo.currentIndexChanged.connect(self._onStatusComboChanged)
        self.auto_reply_btn.clicked.connect(self._on_auto_reply_clicked)

    def _onStatusComboChanged(self, index: int):
        """状态下拉框改变：直接启动状态设置线程（逻辑内聚在卡片内）"""
        new_status = self._comboIndexToStatus(index)
        status_text = self._getStatusText(new_status)
        try:
            self.status_thread = SetStatusThread(self.account_data, new_status)
            self.status_thread.status_set_success.connect(self._on_status_set_success)
            self.status_thread.status_set_failed.connect(self._on_status_set_failed)
            self.status_thread.start()
        except Exception as e:
            logger.error(f"设置{status_text}状态失败: error_type={type(e).__name__}")
            QMessageBox.critical(self, "错误", f"设置{status_text}状态失败，请稍后重试")

    def _on_status_set_success(self, account_data: dict, new_status: int):
        """状态设置成功回调：更新自身徐章/下拉框"""
        try:
            self.updateStatus(new_status)
            status_text = self._getStatusText(new_status)
            logger.info(f"账号 '{self.account_name}' 已成功设置为{status_text}状态")
        except Exception as e:
            logger.error(f"处理状态设置成功回调失败: error_type={type(e).__name__}")

    def _on_status_set_failed(self, account_data: dict, error_message: str):
        """状态设置失败回调（已去除引用不存在方法的 setButtonLoading 遗留死代码）"""
        try:
            logger.error(f"设置账号 '{self.account_name}' 状态失败：{error_message}")
            QMessageBox.warning(self, "失败", f"设置账号 '{self.account_name}' 状态失败：{error_message}")
        except Exception as e:
            logger.error(f"处理状态设置失败回调失败: error_type={type(e).__name__}")

    # ---- 自动回复开关：逻辑内聚在卡片内（只操作自己的按钮 + 全局管理器） ----

    def _on_auto_reply_clicked(self):
        """自动回复开关点击"""
        try:
            if auto_reply_manager.is_running(self.account_data):
                self._stop_auto_reply()
            else:
                self._start_auto_reply()
            # 顶部统计属于父窗口，发信号请求刷新
            self.stats_changed.emit()
        except Exception as e:
            logger.error(f"自动回复开关操作失败: error_type={type(e).__name__}")
            QMessageBox.critical(self, "错误", "自动回复操作失败，请稍后重试")

    def _start_auto_reply(self):
        """启动自动回复"""
        try:
            if self.account_data.get("status") != 1:
                QMessageBox.warning(self, "提示", "账号必须先上线才能开始自动回复！")
                return

            self.auto_reply_btn.setText("启动中...")
            self.auto_reply_btn.setEnabled(False)

            # 创建消息回调
            message_callback = self._make_message_callback()
            success = auto_reply_manager.start_auto_reply(self.account_data, message_callback=message_callback)

            if success:
                self.setAutoReplyStatus(True)
                self.auto_reply_btn.setEnabled(True)
                logger.info(f"账号 '{self.account_name}' 自动回复启动成功")
            else:
                self.auto_reply_btn.setText("开始回复")
                self.auto_reply_btn.setEnabled(True)
                QMessageBox.warning(self, "失败", f"启动账号 '{self.account_name}' 自动回复失败！")
        except Exception as e:
            logger.error(f"启动自动回复失败: error_type={type(e).__name__}")
            self.auto_reply_btn.setText("开始回复")
            self.auto_reply_btn.setEnabled(True)
            QMessageBox.critical(self, "错误", "启动自动回复失败，请稍后重试")

    def _stop_auto_reply(self):
        """停止自动回复"""
        try:
            self.auto_reply_btn.setText("停止中...")
            self.auto_reply_btn.setEnabled(False)

            success = auto_reply_manager.stop_auto_reply(self.account_data)
            self.setAutoReplyStatus(auto_reply_manager.is_running(self.account_data))
            self.auto_reply_btn.setEnabled(True)

            if success:
                logger.info(f"账号 '{self.account_name}' 自动回复停止成功")
            else:
                logger.warning(f"账号 '{self.account_name}' 自动回复停止可能未完全成功")
        except Exception as e:
            logger.error(f"停止自动回复失败: error_type={type(e).__name__}")
            self.setAutoReplyStatus(False)
            QMessageBox.critical(self, "错误", "停止自动回复失败，请稍后重试")

    def _make_message_callback(self):
        """创建消息回调（在后台线程执行）：通过信号把新消息转交协调器，线程安全"""
        def callback(account_data_cb, content, is_self, nickname, timestamp, from_uid):
            try:
                self.new_message.emit(account_data_cb, content, is_self, nickname, timestamp, from_uid)
            except Exception as e:
                logger.debug(f"消息回调处理失败: {e}")
        return callback

    def update_last_message(self, content: str, timestamp: float = None):
        """更新最后一条消息"""
        from .chat_panel import filter_message_content
        clean_content = filter_message_content(content)
        self.last_message = clean_content[:30] + "..." if len(clean_content) > 30 else clean_content
        if timestamp:
            self.last_message_time = datetime.fromtimestamp(timestamp).strftime("%H:%M")
        # 更新消息标签
        if hasattr(self, 'msg_label') and self.msg_label:
            self.msg_label.setText(self.last_message or "暂无消息")

    def setAutoReplyStatus(self, is_running: bool):
        """设置自动回复状态（仅更新文字和图标，不改变按钮可用性）"""
        self.auto_reply_status = is_running
        if is_running:
            self.auto_reply_btn.setText("停止回复")
            self.auto_reply_btn.setIcon(FIF.CANCEL)
        else:
            self.auto_reply_btn.setText("开始回复")
            self.auto_reply_btn.setIcon(FIF.ROBOT)

    def updateStatus(self, new_status: int):
        """更新账号状态"""
        self.account_data["status"] = new_status
        self.status = self._getStatusText(new_status)

        # 更新下拉框（不触发信号）
        combo_index = {1: 0, 2: 1, 3: 2}.get(new_status, 2)
        self.status_combo.blockSignals(True)
        self.status_combo.setCurrentIndex(combo_index)
        self.status_combo.blockSignals(False)

        # 更新状态标签
        old_badge = self.action_widget.layout().itemAt(0).widget()
        if old_badge:
            old_badge.deleteLater()

        new_badge = self.createStatusBadge()
        self.action_widget.layout().insertWidget(0, new_badge, 0, Qt.AlignmentFlag.AlignRight)

    def mousePressEvent(self, event):
        """鼠标点击事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.shop_clicked.emit(self.account_data)
        super().mousePressEvent(event)


__all__ = ['ShopListItem']
