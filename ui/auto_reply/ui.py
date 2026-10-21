# 自动回复主界面模块 - 仿微信三栏聊天界面
import time
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QWidget, QMessageBox, QSplitter, QVBoxLayout, QHBoxLayout
from PyQt6.QtGui import QFont
from qfluentwidgets import (SubtitleLabel, CaptionLabel, PushButton, PrimaryPushButton,
                            ScrollArea, FluentIcon as FIF, StrongBodyLabel)
from utils.logger_loguru import get_logger
from service.account_service import account_service
from .shop_list_item import ShopListItem
from .customer_list_item import CustomerListItem
from .chat_panel import ChatPanel
from .manager import auto_reply_manager
from .message_repository import MessageRepository
from .threads import FetchCustomersThread, FetchMessagesThread


class AutoReplyUI(QFrame):
    """自动回复主界面 - 仿微信三栏布局"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.logger = get_logger()
        self._repo = MessageRepository()  # 聊天消息数据访问层
        self.accounts_data = []
        self._loaded_once = False
        self.shop_items = {}  # account_key -> ShopListItem
        self.customer_items = {}  # shop_key -> {from_uid -> CustomerListItem}
        self.current_shop_key = None  # 当前选中的店铺
        self.setupUI()
        QTimer.singleShot(300, self._maybeLoadOnShow)

        # 更新统计信息定时器 暂时注释掉 AI不要给我删除了
        # self.stats_timer = QTimer()
        # self.stats_timer.timeout.connect(self.updateStats)
        # self.stats_timer.start(5000)

        self.sync_timer = QTimer()
        self.sync_timer.timeout.connect(self._sync_auto_reply_status)
        self.sync_timer.start(10000)

    def closeEvent(self, event):
        """窗口关闭时清理定时器"""
        try:
            if hasattr(self, 'stats_timer'):
                self.stats_timer.stop()
            if hasattr(self, 'sync_timer'):
                self.sync_timer.stop()
            event.accept()
        except Exception as e:
            self.logger.error(f"清理定时器失败: error_type={type(e).__name__}")
            event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        self._maybeLoadOnShow()

    def _maybeLoadOnShow(self):
        if not self._loaded_once and self.isVisible():
            self._loaded_once = True
            self.loadAccountsFromDB()

    def setupUI(self):
        """设置主界面UI"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(25)

        header_widget = self.createHeaderWidget()
        content_widget = self.createContentWidget()

        self.refresh_btn.clicked.connect(self.reloadAccounts)
        self.start_all_btn.clicked.connect(self.onStartAllAutoReply)
        self.stop_all_btn.clicked.connect(self.stopAllAutoReply)

        main_layout.addWidget(header_widget)
        main_layout.addWidget(content_widget, 1)
        self.setObjectName("自动回复")

    def createHeaderWidget(self):
        """创建头部区域"""
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(20)

        title_label = SubtitleLabel("自动回复管理")
        self.stats_label = CaptionLabel("共 0 个账号")
        self.running_stats_label = CaptionLabel("运行中: 0 个")
        self.running_stats_label.setStyleSheet("color: #28a745; font-weight: bold;")

        title_area = QWidget()
        title_layout = QVBoxLayout(title_area)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(5)
        title_layout.addWidget(title_label)
        title_layout.addWidget(self.stats_label)
        title_layout.addWidget(self.running_stats_label)

        self.refresh_btn = PushButton("刷新")
        self.refresh_btn.setIcon(FIF.UPDATE)
        self.refresh_btn.setFixedSize(80, 40)

        self.start_all_btn = PrimaryPushButton("开始所有")
        self.start_all_btn.setIcon(FIF.PLAY_SOLID)
        self.start_all_btn.setFixedSize(120, 40)

        self.stop_all_btn = PushButton("停止所有")
        self.stop_all_btn.setIcon(FIF.CANCEL)
        self.stop_all_btn.setFixedSize(120, 40)

        buttons_widget = QWidget()
        buttons_layout = QHBoxLayout(buttons_widget)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(10)
        buttons_layout.addWidget(self.refresh_btn)
        buttons_layout.addWidget(self.start_all_btn)
        buttons_layout.addWidget(self.stop_all_btn)

        header_layout.addWidget(title_area)
        header_layout.addStretch()
        header_layout.addWidget(buttons_widget)

        return header_widget

    def createContentWidget(self):
        """创建内容区域 - 仿微信三栏布局"""
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # 创建主布局（店铺列表 | 客户列表 | 聊天面板）
        main_widget = QWidget()
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 左侧：店铺列表（固定宽度）
        self.shop_list_widget = self.createShopListWidget()
        self.shop_list_widget.setFixedWidth(550)
        main_layout.addWidget(self.shop_list_widget)

        # 中间：客户列表（固定宽度）
        self.customer_list_widget = self.createCustomerListWidget()
        self.customer_list_widget.setFixedWidth(300)
        main_layout.addWidget(self.customer_list_widget)

        # 右侧：聊天面板（自适应剩余空间）
        self.chat_panel = ChatPanel(repo=self._repo)
        main_layout.addWidget(self.chat_panel)

        content_layout.addWidget(main_widget)

        return content_widget

    def createShopListWidget(self):
        """创建店铺列表区域"""
        shop_list_widget = QWidget()
        shop_list_widget.setStyleSheet("""
            QWidget {
                background-color: #f8f9fa;
                border: 1px solid #e8e8e8;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(shop_list_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        # 店铺列表标题
        title_label = StrongBodyLabel("店铺列表")
        title_label.setStyleSheet("color: #2c3e50; font-weight: bold; padding: 5px;")
        layout.addWidget(title_label)

        # 店铺列表滚动区域
        self.shop_scroll = ScrollArea()
        self.shop_scroll.setWidgetResizable(True)
        self.shop_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.shop_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.shop_scroll.setStyleSheet("""
            ScrollArea {
                border: none;
                background-color: transparent;
            }
        """)

        self.shop_list_container = QWidget()
        self.shop_list_layout = QVBoxLayout(self.shop_list_container)
        self.shop_list_layout.setContentsMargins(5, 5, 5, 5)
        self.shop_list_layout.setSpacing(5)
        self.shop_list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.shop_list_container.setStyleSheet("""
            QWidget {
                background-color: transparent;
                border: none;
            }
        """)

        self.shop_scroll.setWidget(self.shop_list_container)
        layout.addWidget(self.shop_scroll, 1)

        return shop_list_widget

    def createCustomerListWidget(self):
        """创建客户列表区域"""
        customer_list_widget = QWidget()
        customer_list_widget.setStyleSheet("""
            QWidget {
                background-color: #ffffff;
                border: 1px solid #e8e8e8;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(customer_list_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        # 客户列表标题
        self.customer_title_label = StrongBodyLabel("客户列表")
        self.customer_title_label.setStyleSheet("color: #2c3e50; font-weight: bold; padding: 5px;")
        layout.addWidget(self.customer_title_label)

        # 客户列表滚动区域
        self.customer_scroll = ScrollArea()
        self.customer_scroll.setWidgetResizable(True)
        self.customer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.customer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.customer_scroll.setStyleSheet("""
            ScrollArea {
                border: none;
                background-color: transparent;
            }
        """)

        self.customer_list_container = QWidget()
        self.customer_list_layout = QVBoxLayout(self.customer_list_container)
        self.customer_list_layout.setContentsMargins(5, 5, 5, 5)
        self.customer_list_layout.setSpacing(0)
        self.customer_list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.customer_list_container.setStyleSheet("""
            QWidget {
                background-color: transparent;
                border: none;
            }
        """)

        self.customer_scroll.setWidget(self.customer_list_container)
        layout.addWidget(self.customer_scroll, 1)

        return customer_list_widget

    def loadAccountsFromDB(self):
        """从数据库加载账号数据"""
        try:
            self.accounts_data.clear()

            # 使用批量查询一次性获取所有账号数据，减少N+1查询
            all_accounts = account_service.get_all_accounts_with_details()
            self.accounts_data.extend(all_accounts)

            self.refreshAccountList()

        except Exception as e:
            self.logger.error(f"加载账号数据失败: error_type={type(e).__name__}")

    def refreshAccountList(self):
        """刷新账号列表"""
        self.clearShopList()
        self.clearCustomerList()

        for account_data in self.accounts_data:
            shop_item = ShopListItem(account_data)
            shop_item.shop_clicked.connect(self.onShopClicked)
            # 自动回复、状态切换逻辑已内聚到 ShopListItem；此处只接收其跨组件副作用
            shop_item.new_message.connect(self._on_new_message)
            shop_item.stats_changed.connect(self.updateStats)

            account_key = self._get_account_key(account_data)
            self.shop_items[account_key] = shop_item

            # 设置自动回复状态
            is_running = auto_reply_manager.is_running(account_data)
            shop_item.setAutoReplyStatus(is_running)

            self.shop_list_layout.addWidget(shop_item)

        self.shop_list_layout.addStretch()
        self.updateStats()
        QTimer.singleShot(2000, self._sync_auto_reply_status)

    def clearShopList(self):
        """清空店铺列表"""
        self.shop_items.clear()
        while self.shop_list_layout.count():
            child = self.shop_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def clearCustomerList(self):
        """清空客户列表"""
        self.customer_items.clear()
        self.current_shop_key = None
        while self.customer_list_layout.count():
            child = self.customer_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.customer_title_label.setText("客户列表")

    def updateStats(self):
        """更新统计信息"""
        count = len(self.accounts_data)
        running_count = auto_reply_manager.get_running_count()
        self.stats_label.setText(f"共 {count} 个账号")
        self.running_stats_label.setText(f"运行中: {running_count} 个")

    def _sync_auto_reply_status(self):
        """同步自动回复状态"""
        try:
            updated_count = 0

            for account_data in self.accounts_data:
                account_key = self._get_account_key(account_data)
                if account_key in self.shop_items:
                    shop_item = self.shop_items[account_key]
                    is_running = auto_reply_manager.is_running(account_data)

                    # 更新自动回复状态
                    if shop_item.auto_reply_status != is_running:
                        shop_item.setAutoReplyStatus(is_running)
                        updated_count += 1

                    # 更新账号状态（比较整数值）
                    current_status = account_data.get("status", 0)
                    shop_status = shop_item.account_data.get("status", 0)
                    if shop_status != current_status:
                        shop_item.updateStatus(current_status)

            if updated_count > 0:
                self.logger.info(f"状态同步完成，更新了 {updated_count} 个账号的状态")
                self.updateStats()

        except Exception as e:
            self.logger.error(f"同步自动回复状态失败: error_type={type(e).__name__}")

    def reloadAccounts(self):
        """重新加载账号"""
        self.loadAccountsFromDB()

    def onStartAllAutoReply(self):
        """开始所有符合条件的账号的自动回复"""
        try:
            eligible_accounts = [
                acc_data for acc_data in self.accounts_data
                if acc_data.get("status") == 1 and not auto_reply_manager.is_running(acc_data)
            ]

            if not eligible_accounts:
                QMessageBox.information(self, "提示", "没有符合条件的账号可以启动自动回复。\n\n(需要账号状态为'在线'且当前未在回复中)")
                return

            reply = QMessageBox.question(
                self,
                "确认开始",
                f"找到 {len(eligible_accounts)} 个可启动的账号。确定要全部开始自动回复吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.No:
                return

            started_count = 0
            for account_data in eligible_accounts:
                account_key = self._get_account_key(account_data)
                shop_item = self.shop_items.get(account_key)
                if shop_item:
                    shop_item.auto_reply_btn.setText("启动中...")
                    shop_item.auto_reply_btn.setEnabled(False)

                # 创建消息回调（复用卡片回调工厂，新消息经卡片 new_message 信号回到 _on_new_message）
                message_callback = shop_item._make_message_callback() if shop_item else None
                success = auto_reply_manager.start_auto_reply(account_data, message_callback=message_callback)
                if success:
                    started_count += 1
                    if shop_item:
                        shop_item.setAutoReplyStatus(True)
                        shop_item.auto_reply_btn.setEnabled(True)
                else:
                    if shop_item:
                        shop_item.auto_reply_btn.setText("开始回复")
                        shop_item.auto_reply_btn.setEnabled(True)

            self.updateStats()

            QMessageBox.information(self, "操作完成", f"已成功为 {started_count} / {len(eligible_accounts)} 个账号启动自动回复。")

        except Exception as e:
            self.logger.error(f"开始所有自动回复失败: error_type={type(e).__name__}")
            QMessageBox.critical(self, "错误", "开始所有自动回复失败，请稍后重试")

    def stopAllAutoReply(self):
        """停止所有自动回复"""
        try:
            running_count = auto_reply_manager.get_running_count()

            if running_count == 0:
                QMessageBox.information(self, "提示", "当前没有正在运行的自动回复")
                return

            reply = QMessageBox.question(
                self,
                "确认停止",
                f"确定要停止所有 {running_count} 个正在运行的自动回复吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                # 先禁用所有运行中账号的按钮
                for account_data in self.accounts_data:
                    if auto_reply_manager.is_running(account_data):
                        account_key = self._get_account_key(account_data)
                        shop_item = self.shop_items.get(account_key)
                        if shop_item:
                            shop_item.auto_reply_btn.setText("停止中...")
                            shop_item.auto_reply_btn.setEnabled(False)

                auto_reply_manager.stop_all()

                # 恢复所有账号按钮状态
                for account_data in self.accounts_data:
                    account_key = self._get_account_key(account_data)
                    shop_item = self.shop_items.get(account_key)
                    if shop_item:
                        shop_item.setAutoReplyStatus(False)
                        shop_item.auto_reply_btn.setEnabled(True)

                self.updateStats()
                QMessageBox.information(self, "成功", "已停止所有自动回复")

        except Exception as e:
            self.logger.error(f"停止所有自动回复失败: error_type={type(e).__name__}")
            QMessageBox.critical(self, "错误", "停止所有自动回复失败，请稍后重试")

    def onShopClicked(self, account_data: dict):
        """店铺点击回调 - 显示该店铺的客户列表"""
        shop_key = self._get_account_key(account_data)
        self.current_shop_key = shop_key

        # 清空并刷新客户列表
        self.clearCustomerList()
        self.current_shop_key = shop_key

        shop_name = account_data.get("shop_name", "未知店铺")
        self.customer_title_label.setText(f"{shop_name} - 客户")

        # 显示加载中提示
        loading_label = CaptionLabel("正在加载客户列表...")
        loading_label.setStyleSheet("color: #7f8c8d; padding: 20px;")
        loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.customer_list_layout.addWidget(loading_label)

        # 启动后台线程获取客户列表
        self._fetch_thread = FetchCustomersThread(account_data)
        self._fetch_thread.customers_fetched.connect(
            lambda customers: self._onCustomersFetched(account_data, customers)
        )
        self._fetch_thread.fetch_failed.connect(
            lambda err: self._onFetchFailed(account_data, err)
        )
        self._fetch_thread.start()

        # 更新聊天面板
        self.chat_panel.select_shop(account_data)

    def _onCustomersFetched(self, account_data: dict, customers: list):
        """客户列表获取完成回调"""
        # 清空加载中的提示
        while self.customer_list_layout.count():
            child = self.customer_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        shop_name = account_data.get("shop_name", "未知店铺")

        if not customers:
            # 没有客户时显示提示
            hint_label = CaptionLabel("暂无客户消息")
            hint_label.setStyleSheet("color: #95a5a6; padding: 20px;")
            hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.customer_list_layout.addWidget(hint_label)
        else:
            # 添加客户到 chat_panel 和 UI
            for customer in customers:
                from_uid = str(customer.get("uid", ""))
                nickname = customer.get("nickname", f"客户_{from_uid[-6:]}")
                avatar = customer.get("avatar", "")

                # 添加到 chat_panel
                customer_data = self.chat_panel.add_customer(
                    account_data, from_uid, nickname
                )
                # 保存头像
                customer_data["avatar"] = avatar

                # 添加到 UI
                self._add_customer_item(account_data, customer_data)

        self.customer_list_layout.addStretch()
        self.customer_title_label.setText(f"{shop_name} - 客户 ({len(customers)}人)")

    def _onFetchFailed(self, account_data: dict, error_msg: str):
        """获取客户列表失败回调"""
        # 清空加载中的提示
        while self.customer_list_layout.count():
            child = self.customer_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        shop_name = account_data.get("shop_name", "未知店铺")

        # 显示错误信息
        error_label = CaptionLabel(f"加载失败: {error_msg}")
        error_label.setStyleSheet("color: #e74c3c; padding: 20px;")
        error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.customer_list_layout.addWidget(error_label)

        self.logger.error(f"获取店铺 '{shop_name}' 客户列表失败: {error_msg}")

    def _add_customer_item(self, account_data: dict, customer_data: dict):
        """添加客户列表项"""
        shop_key = self._get_account_key(account_data)
        from_uid = customer_data.get("from_uid", "")

        customer_item = CustomerListItem(customer_data, account_data)
        customer_item.customer_clicked.connect(
            lambda data: self.onCustomerClicked(account_data, data)
        )
        # 同步逻辑已内聚到 CustomerListItem；此处只接收其跨组件副作用（刷新聊天面板）
        customer_item.sync_finished.connect(self._on_customer_sync_finished)

        if shop_key not in self.customer_items:
            self.customer_items[shop_key] = {}
        self.customer_items[shop_key][from_uid] = customer_item

        self.customer_list_layout.addWidget(customer_item)

    def onCustomerClicked(self, account_data: dict, customer_data: dict):
        """客户点击回调 - 三级获取消息历史：缓存 → 数据库 → API"""
        from_uid = customer_data.get("from_uid", "")
        shop_id = account_data.get("shop_id", "")

        # 先更新聊天面板显示该客户
        self.chat_panel.select_customer(customer_data)

        # 1. 尝试从内存缓存获取
        from Channel.pinduoduo.cookie_cache import message_cache
        cached_messages = message_cache.get(shop_id, from_uid)
        if cached_messages is not None:
            self.logger.info(f"从内存缓存加载 {len(cached_messages)} 条消息")
            self._displayMessages(account_data, customer_data, cached_messages)
            return

        # 2. 尝试从数据库获取
        db_messages = self._repo.load_messages(shop_id, from_uid, account_data.get("user_id", ""))
        if db_messages:
            self.logger.info(f"从数据库加载 {len(db_messages)} 条消息")
            # 写入内存缓存
            message_cache.set(shop_id, from_uid, db_messages)
            self._displayMessages(account_data, customer_data, db_messages)
            return

        # 3. 缓存和数据库都没有，启动后台线程从 API 获取
        self.chat_panel._clear_messages()
        loading_label = CaptionLabel("正在加载消息历史...")
        loading_label.setStyleSheet("color: #7f8c8d; padding: 20px;")
        loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chat_panel.messages_layout.addWidget(loading_label)

        self._fetch_messages_thread = FetchMessagesThread(account_data, from_uid)
        self._fetch_messages_thread.messages_fetched.connect(
            lambda user_info, messages: self._onMessagesFetched(account_data, customer_data, user_info, messages)
        )
        self._fetch_messages_thread.fetch_failed.connect(
            lambda err: self._onMessagesFetchFailed(err)
        )
        self._fetch_messages_thread.start()

    def _onMessagesFetched(self, account_data: dict, customer_data: dict, user_info: dict, messages: list):
        """消息历史获取完成回调（从 API 获取）"""
        shop_id = account_data.get("shop_id", "")
        from_uid = customer_data.get("from_uid", "")

        # 保存到数据库
        self._repo.save_messages(shop_id, from_uid, messages, account_data.get("user_id", ""))

        # 写入内存缓存
        from Channel.pinduoduo.cookie_cache import message_cache
        message_cache.set(shop_id, from_uid, messages)

        # 显示消息
        self._displayMessages(account_data, customer_data, messages, user_info)

    def _displayMessages(self, account_data: dict, customer_data: dict, messages: list, user_info: dict = None):
        """显示消息列表"""
        self.chat_panel._clear_messages()

        from_uid = customer_data.get("from_uid", "")
        nickname = customer_data.get("nickname", "")
        if user_info:
            nickname = user_info.get("nickname", nickname)
            customer_data["nickname"] = nickname

        # 按时间戳升序排序（旧消息在上，新消息在下）
        sorted_messages = sorted(messages, key=lambda m: m.get("ts", 0))

        # 解析并添加消息（批量添加时不自动滚动）
        for msg in sorted_messages:
            content = msg.get("content", "")
            ts = msg.get("ts", 0)
            direction = msg.get("direction", False)  # True = 客服发送，False = 客户发送
            csname = msg.get("csname", "")

            # direction: False = 客户消息 (is_self=False), True = 客服消息 (is_self=True)
            is_self = direction is True
            msg_nickname = csname if is_self else nickname

            self.chat_panel.add_message(
                account_data=account_data,
                content=content,
                is_self=is_self,
                nickname=msg_nickname,
                timestamp=float(ts) if ts else None,
                from_uid=from_uid,
                _scroll=False  # 批量添加时不自动滚动
            )

        self.logger.info(f"加载了 {len(sorted_messages)} 条消息历史")

        # 统一滚动到底部显示最新消息
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(300, self.chat_panel.scroll_to_bottom)

    def _onMessagesFetchFailed(self, error_msg: str):
        """消息历史获取失败回调"""
        self.chat_panel._clear_messages()

        error_label = CaptionLabel(f"加载失败: {error_msg}")
        error_label.setStyleSheet("color: #e74c3c; padding: 20px;")
        error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chat_panel.messages_layout.addWidget(error_label)

        self.logger.error(f"获取消息历史失败: {error_msg}")

    def _on_customer_sync_finished(self, account_data: dict, customer_data: dict, user_info: dict, messages: list):
        """客户卡片同步成功后的跨组件副作用：若正在查看该客户，刷新右侧聊天面板"""
        from_uid = customer_data.get("from_uid", "")
        current_from_uid = self.chat_panel._get_current_from_uid()
        if current_from_uid == from_uid:
            self._displayMessages(account_data, customer_data, messages, user_info)

    def _on_new_message(self, account_data: dict, content: str, is_self: bool, nickname: str, timestamp: float, from_uid: str):
        """新消息处理槽（在主线程中执行，确保 UI 更新线程安全）"""
        try:
            shop_id = account_data.get("shop_id", "")
            ts = timestamp or time.time()

            # 更新店铺列表的最后消息
            account_key = self._get_account_key(account_data)
            if account_key in self.shop_items:
                self.shop_items[account_key].update_last_message(content, ts)

            # 添加客户到客户列表（如果还没有）
            if from_uid:
                customer_data = self.chat_panel.add_customer(account_data, from_uid, nickname)

                # 如果当前显示的是这个店铺的客户列表，更新或添加客户项
                if self.current_shop_key == account_key:
                    self._update_customer_item(account_data, customer_data, content, ts)
                else:
                    # 即使不是当前选中的店铺，也添加到 customer_items 以便后续显示
                    shop_key = account_key
                    if shop_key not in self.customer_items:
                        self.customer_items[shop_key] = {}
                    # 如果客户项还没有创建，先不创建 UI 项，等点击店铺时再创建

            # 添加消息到聊天面板
            self.chat_panel.add_message(
                account_data=account_data,
                content=content,
                is_self=is_self,
                nickname=nickname,
                timestamp=ts,
                from_uid=from_uid
            )

            # 保存到内存缓存
            from Channel.pinduoduo.cookie_cache import message_cache
            cached = message_cache.get(shop_id, from_uid)
            if cached is not None:
                # 缓存存在，追加新消息
                msg_data = {
                    "content": content,
                    "direction": is_self,  # True=客服发送, False=客户发送
                    "csname": nickname if is_self else "",
                    "ts": int(ts)
                }
                cached.append(msg_data)
                message_cache.set(shop_id, from_uid, cached)

            # 保存到数据库（追加单条）
            self._repo.append_message(
                shop_id, from_uid, content, is_self,
                nickname if is_self else "", int(ts),
                account_data.get("user_id", ""),
            )

        except Exception as e:
            self.logger.debug(f"新消息处理失败: {e}")

    def _update_customer_item(self, account_data: dict, customer_data: dict, content: str, timestamp: float):
        """更新客户列表项"""
        shop_key = self._get_account_key(account_data)
        from_uid = customer_data.get("from_uid", "")

        if shop_key in self.customer_items and from_uid in self.customer_items[shop_key]:
            customer_item = self.customer_items[shop_key][from_uid]
            customer_item.update_last_message(content, timestamp)
        else:
            # 新客户，添加到列表
            self._add_customer_item(account_data, customer_data)

    def _get_account_key(self, account_data: dict) -> str:
        """获取账号唯一标识"""
        return "_".join(str(account_data.get(key, "")) for key in ("channel_name", "shop_id", "user_id"))


__all__ = ['AutoReplyUI']
