# 后台线程模块
import asyncio
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter, QPainterPath
from PyQt6.QtCore import Qt
from utils.logger_loguru import get_logger
from utils.safe_image_fetch import fetch_image


class LogoLoaderThread(QThread):
    """异步加载Logo的线程"""
    logo_loaded = pyqtSignal(QPixmap)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            image_data = fetch_image(self.url)

            pixmap = QPixmap()
            pixmap.loadFromData(image_data)

            if pixmap.isNull():
                raise ValueError("Loaded data is not a valid image.")

            # 创建圆形pixmap
            size = 60
            circular_pixmap = QPixmap(size, size)
            circular_pixmap.fill(Qt.GlobalColor.transparent)

            painter = QPainter(circular_pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            path = QPainterPath()
            path.addEllipse(0, 0, size, size)

            painter.setClipPath(path)

            # 缩放并绘制原始图片
            scaled_pixmap = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(0, 0, scaled_pixmap)
            painter.end()

            self.logo_loaded.emit(circular_pixmap)
        except Exception as e:
            get_logger().error(f"Failed to load logo: error_type={type(e).__name__}:{e}")
            self.logo_loaded.emit(QPixmap())


class AutoReplyThread(QThread):
    """自动回复线程 - 每个账号独立的WebSocket连接线程"""

    connection_success = pyqtSignal()  # 连接成功信号
    connection_failed = pyqtSignal(str)  # 连接失败信号
    message_received = pyqtSignal(dict, str, bool, str, float, str)  # 消息接收信号 (account_data, content, is_self, nickname, timestamp, from_uid)

    def __init__(self, account_data: dict, message_callback=None):
        super().__init__()
        self.account_data = account_data
        self.message_callback = message_callback  # 消息回调函数
        self.channel = None
        self.loop = None
        self._shutdown_task = None
        self._stop_requested = False
        self.logger = get_logger("AutoReplyThread")

    def run(self):
        """启动后端 PDDChannel 引擎"""
        from Channel.pinduoduo.pdd_channel import PDDChannel

        try:
            # 为当前线程创建并设置新的事件循环
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            # 创建 PDDChannel 实例
            self.channel = PDDChannel()

            # 设置消息回调
            if self.message_callback:
                self.channel._message_callback = self.message_callback

            # 定义成功和失败的回调函数
            def on_success():
                self.connection_success.emit()

            def on_failure(error_msg):
                self.connection_failed.emit(error_msg)

            # 启动引擎，并传递回调
            self._start_task = self.loop.create_task(
                self.channel.start_account(
                    shop_id=self.account_data['shop_id'],
                    user_id=self.account_data['user_id'],
                    on_success=on_success,
                    on_failure=on_failure
                )
            )

            # 保持事件循环运行，直到显式停止
            self.loop.run_forever()

        except Exception as e:
            self.logger.error(f"自动回复线程启动失败: {type(e).__name__}")
            self.connection_failed.emit("自动回复线程启动失败，请稍后重试")
        finally:
            loop = self.loop
            if loop is not None:
                try:
                    if loop.is_running():
                        loop.stop()

                    # 先在事件循环内取消并等待剩余任务，再关闭 loop，避免
                    # cleanup 协程在 loop 关闭后继续访问 asyncio 资源。
                    if not loop.is_closed():
                        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                        for task in pending:
                            task.cancel()
                        if pending:
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                        loop.run_until_complete(loop.shutdown_asyncgens())
                        loop.close()
                except Exception as cleanup_error:
                    self.logger.error("自动回复线程清理失败: error_type={type(cleanup_error).__name__}")
                finally:
                    try:
                        if asyncio.get_event_loop() is loop:
                            asyncio.set_event_loop(None)
                    except RuntimeError:
                        pass

    async def _shutdown_async(self):
        """在工作线程自己的事件循环中完成连接和任务清理。"""
        try:
            if self.channel:
                shop_id = self.account_data.get("shop_id")
                user_id = self.account_data.get("user_id")
                if shop_id is not None and user_id is not None:
                    await asyncio.wait_for(
                        self.channel.stop_account(shop_id, user_id),
                        timeout=4.0,
                    )
                else:
                    await asyncio.wait_for(
                        self.channel.stop_all_connections(),
                        timeout=4.0,
                    )
        except asyncio.TimeoutError:
            self.logger.warning("自动回复连接清理超时，将取消剩余任务")
        except Exception as e:
            self.logger.warning(
                "自动回复连接清理失败，将取消剩余任务: "
                f"error_type={type(e).__name__}"
            )
        finally:
            current_task = asyncio.current_task()
            pending = [
                task for task in asyncio.all_tasks()
                if task is not current_task and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            asyncio.get_running_loop().stop()

    def stop(self):
        """停止后端引擎"""
        try:
            loop = self.loop
            if loop is None or loop.is_closed() or not loop.is_running():
                return
            if self._stop_requested:
                return

            self._stop_requested = True

            # stop() 通常从 Qt 主线程调用；所有 asyncio 对象必须回到其
            # 所属事件循环线程中操作，不能直接跨线程 cancel/set。
            def schedule_shutdown():
                if loop.is_closed():
                    return
                if self._shutdown_task is None or self._shutdown_task.done():
                    self._shutdown_task = loop.create_task(self._shutdown_async())

            loop.call_soon_threadsafe(schedule_shutdown)

        except Exception as e:
            self.logger.error(
                f"停止自动回复线程失败: error_type={type(e).__name__}"
            )

    def is_running(self) -> bool:
        """检查线程是否在运行"""
        # 实际的运行状态由 PDDChannel 内部管理，这里仅表示线程是否已启动
        return self.isRunning()


class SetStatusThread(QThread):
    """设置账号状态的线程"""

    status_set_success = pyqtSignal(dict, int)  # 设置成功信号
    status_set_failed = pyqtSignal(dict, str)   # 设置失败信号

    def __init__(self, account_data: dict, target_status: int):
        super().__init__()
        self.account_data = account_data
        self.target_status = target_status
        self.logger = get_logger()

    def run(self):
        """在后台线程中执行状态更新"""
        from Channel.pinduoduo.utils.API.Set_up_online import AccountMonitor
        from service.account_service import account_service

        try:
            # 1. 调用API设置平台状态
            cookies = self.account_data.get("cookies")
            if not cookies:
                raise ValueError("账号缺少cookies，无法设置状态")

            # 获取账户信息用于自动重新登录
            shop_id = self.account_data.get("shop_id")
            user_id = self.account_data.get("user_id")
            channel_name = self.account_data.get("channel_name", "pinduoduo")

            account_monitor = AccountMonitor(cookies, shop_id=shop_id, user_id=user_id, channel_name=channel_name)

            api_success = account_monitor.set_csstatus(self.target_status)

            if not api_success:
                # API调用失败
                self.status_set_failed.emit(self.account_data, "平台状态设置失败")
                return

            # 2. 更新数据库状态
            db_success = account_service.update_account_status(
                channel_name=self.account_data["channel_name"],
                shop_id=self.account_data["shop_id"],
                user_id=self.account_data["user_id"],
                status=self.target_status
            )

            if db_success:
                # 发射成功信号
                self.status_set_success.emit(self.account_data, self.target_status)
            else:
                # 发射失败信号
                self.status_set_failed.emit(self.account_data, "数据库状态更新失败")

        except KeyError:
            # 如果缺少 'user_id' 等关键信息
            self.status_set_failed.emit(self.account_data, "账号数据不完整，无法设置状态")
        except Exception as e:
            # 其他异常
            self.status_set_failed.emit(self.account_data, str(e))


class FetchCustomersThread(QThread):
    """异步获取客户列表的线程"""

    customers_fetched = pyqtSignal(list)  # 客户列表获取完成信号
    fetch_failed = pyqtSignal(str)  # 获取失败信号

    def __init__(self, account_data: dict):
        super().__init__()
        self.account_data = account_data
        self.logger = get_logger()

    def run(self):
        try:
            shop_id = self.account_data.get("shop_id", "")
            user_id = self.account_data.get("user_id", "")

            # 从 cookie_cache 获取 cookies
            from Channel.pinduoduo.cookie_cache import cookie_cache
            cookies = cookie_cache.get("pinduoduo", shop_id, user_id)

            if not cookies:
                # 尝试从数据库获取
                from database.db_manager import db_manager
                account_info = db_manager.get_account("pinduoduo", shop_id, user_id)
                if account_info:
                    cookies_data = account_info.get('cookies')
                    if isinstance(cookies_data, str):
                        import json
                        try:
                            cookies = json.loads(cookies_data)
                        except json.JSONDecodeError:
                            cookies = None
                    elif isinstance(cookies_data, dict):
                        cookies = cookies_data

            if not cookies:
                self.fetch_failed.emit("未找到有效的 Cookie，请先登录")
                return

            # 调用 API 获取客户列表
            from Channel.pinduoduo.utils.API.get_messages_users import GetMessagesUsers
            api = GetMessagesUsers(cookies)
            result = api.get_messages_users()

            if result is False:
                self.fetch_failed.emit("获取客户列表失败")
                return

            # 提取客户列表
            conv_users = result.get("convUsers", [])
            self.customers_fetched.emit(conv_users)

        except Exception as e:
            self.logger.error(f"获取客户列表失败: error_type={type(e).__name__}:{e}")
            self.fetch_failed.emit(str(e))


class FetchMessagesThread(QThread):
    """异步获取客户消息历史的线程"""

    messages_fetched = pyqtSignal(dict, list)  # 消息获取完成信号 (customer_info, messages)
    fetch_failed = pyqtSignal(str)  # 获取失败信号

    def __init__(self, account_data: dict, from_uid: str):
        super().__init__()
        self.account_data = account_data
        self.from_uid = from_uid
        self.logger = get_logger()

    def run(self):
        try:
            shop_id = self.account_data.get("shop_id", "")
            user_id = self.account_data.get("user_id", "")
            username = self.account_data.get("username", "")
            password = self.account_data.get("password", "")

            # 从 cookie_cache 获取 cookies
            from Channel.pinduoduo.cookie_cache import cookie_cache
            cookies = cookie_cache.get("pinduoduo", shop_id, user_id)

            if not cookies:
                # 尝试从数据库获取
                from database.db_manager import db_manager
                account_info = db_manager.get_account("pinduoduo", shop_id, user_id)
                if account_info:
                    cookies_data = account_info.get('cookies')
                    if isinstance(cookies_data, str):
                        import json
                        try:
                            cookies = json.loads(cookies_data)
                        except json.JSONDecodeError:
                            cookies = None
                    elif isinstance(cookies_data, dict):
                        cookies = cookies_data

            if not cookies:
                self.fetch_failed.emit("未找到有效的 Cookie，请先登录")
                return

            # 获取 anti-content（优先从缓存获取）
            from Channel.pinduoduo.cookie_cache import anti_content_cache
            anti_content = anti_content_cache.get(shop_id, user_id)

            if not anti_content:
                # 缓存未命中，尝试从 cookies 中获取
                cookies_anti = cookies.get('anti_content') or cookies.get('anti-content', '')
                if cookies_anti:
                    anti_content = cookies_anti
                    # 写入缓存
                    anti_content_cache.set(shop_id, user_id, anti_content)

            if not anti_content and username and password:
                # 尝试通过 Playwright 浏览器获取
                self.logger.info("缓存和 Cookie 中未找到 anti-content，尝试通过浏览器获取...")
                anti_content = self._get_anti_content_from_browser(username, password, shop_id, user_id)
                if anti_content:
                    # 写入缓存
                    anti_content_cache.set(shop_id, user_id, anti_content)

            if not anti_content:
                self.fetch_failed.emit("无法获取 anti-content，请先重新登录账号")
                return

            # 调用 API 获取所有消息历史（自动分页）
            from Channel.pinduoduo.utils.API.get_messages import GetMessages
            api = GetMessages(cookies)
            result = api.get_all_messages(uid=self.from_uid, captured_anti_content=anti_content)

            # 如果失败且有账号密码，尝试通过 Playwright 重新获取 anti-content 再重试一次
            if result is False and username and password:
                self.logger.info("API 调用失败，尝试通过浏览器重新获取 anti-content...")
                from Channel.pinduoduo.cookie_cache import anti_content_cache
                anti_content_cache.invalidate(shop_id, user_id)  # 清除旧缓存
                new_anti_content = self._get_anti_content_from_browser(username, password, shop_id, user_id)
                if new_anti_content:
                    anti_content_cache.set(shop_id, user_id, new_anti_content)
                    # 用新的 anti-content 重试
                    result = api.get_all_messages(uid=self.from_uid, captured_anti_content=new_anti_content)

            if result is False:
                self.fetch_failed.emit("获取消息历史失败")
                return

            # 提取客户信息和消息列表
            user_info = result.get("userInfo", {})
            content_list = result.get("contentList", [])

            self.messages_fetched.emit(user_info, content_list)

        except Exception as e:
            self.logger.error(f"获取消息历史失败: error_type={type(e).__name__}:{e}")
            self.fetch_failed.emit(str(e))

    def _get_anti_content_from_browser(self, username: str, password: str, shop_id: str, user_id: str) -> str:
        """通过 Playwright 浏览器获取 anti-content"""
        import asyncio
        try:
            # 创建新的事件循环（如果在已有循环中运行则需要处理）
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 如果已有循环在运行，创建新线程来运行异步代码
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            lambda: asyncio.run(self._async_get_anti_content(username, password, shop_id, user_id))
                        )
                        return future.result(timeout=60)
                else:
                    return loop.run_until_complete(
                        self._async_get_anti_content(username, password, shop_id, user_id)
                    )
            except RuntimeError:
                # 没有事件循环，创建新的
                return asyncio.run(
                    self._async_get_anti_content(username, password, shop_id, user_id)
                )
        except Exception as e:
            self.logger.error(f"通过浏览器获取 anti-content 失败: {e}")
            return ""

    async def _async_get_anti_content(self, username: str, password: str, shop_id: str, user_id: str) -> str:
        """异步获取 anti-content"""
        from Channel.pinduoduo.pdd_login import PDDLogin
        profile_scope = f"pinduoduo_{shop_id}_{user_id}"
        pdd_login = PDDLogin(name=username, password=password, profile_scope=profile_scope)
        result = await pdd_login.get_anti_content()
        if result and result is not False:
            return result
        return ""


class SyncMessagesThread(QThread):
    """同步消息到数据库的线程"""

    sync_success = pyqtSignal(dict, list)  # 同步成功信号 (customer_info, messages)
    sync_failed = pyqtSignal(str)  # 同步失败信号

    def __init__(self, account_data: dict, from_uid: str):
        super().__init__()
        self.account_data = account_data
        self.from_uid = from_uid
        self.logger = get_logger()

    def run(self):
        try:
            shop_id = self.account_data.get("shop_id", "")
            user_id = self.account_data.get("user_id", "")
            username = self.account_data.get("username", "")
            password = self.account_data.get("password", "")

            # 从 cookie_cache 获取 cookies
            from Channel.pinduoduo.cookie_cache import cookie_cache
            cookies = cookie_cache.get("pinduoduo", shop_id, user_id)

            if not cookies:
                # 内存缓存未命中，尝试从数据库获取
                from database.db_manager import db_manager
                account_info = db_manager.get_account("pinduoduo", shop_id, user_id)
                if account_info:
                    cookies_data = account_info.get('cookies')
                    if isinstance(cookies_data, str):
                        import json
                        try:
                            cookies = json.loads(cookies_data)
                        except json.JSONDecodeError:
                            cookies = None
                    elif isinstance(cookies_data, dict):
                        cookies = cookies_data
                    # 写入内存缓存供后续使用
                    if cookies:
                        cookie_cache.set("pinduoduo", shop_id, user_id, cookies)

            if not cookies:
                self.sync_failed.emit("未找到 Cookie，请先登录")
                return

            # 获取 anti-content
            from Channel.pinduoduo.cookie_cache import anti_content_cache, message_cache
            anti_content = anti_content_cache.get(shop_id, user_id)

            if not anti_content:
                cookies_anti = cookies.get('anti_content') or cookies.get('anti-content', '')
                if cookies_anti:
                    anti_content = cookies_anti
                    anti_content_cache.set(shop_id, user_id, anti_content)

            if not anti_content and username and password:
                self.logger.info("缓存和 Cookie 中未找到 anti-content，尝试通过浏览器获取...")
                anti_content = self._get_anti_content_from_browser(username, password, shop_id, user_id)
                if anti_content:
                    anti_content_cache.set(shop_id, user_id, anti_content)

            if not anti_content:
                self.sync_failed.emit("无法获取 anti-content，请重试")
                return

            # 调用 API 获取所有消息
            from Channel.pinduoduo.utils.API.get_messages import GetMessages
            api = GetMessages(cookies)
            result = api.get_all_messages(uid=self.from_uid, captured_anti_content=anti_content)

            # 如果失败，尝试重新获取 anti-content 并重试
            if result is False and username and password:
                self.logger.info("API 调用失败，尝试通过浏览器重新获取 anti-content...")
                anti_content_cache.invalidate(shop_id, user_id)
                new_anti_content = self._get_anti_content_from_browser(username, password, shop_id, user_id)
                if new_anti_content:
                    anti_content_cache.set(shop_id, user_id, new_anti_content)
                    result = api.get_all_messages(uid=self.from_uid, captured_anti_content=new_anti_content)

            if result is False:
                self.sync_failed.emit("API 调用失败，请检查网络或重试")
                return

            content_list = result.get("contentList", [])
            total = result.get("total", 0)

            self.logger.info(f"获取到 {len(content_list)} 条消息 (总计: {total})")

            # 保存到数据库（使用与 bridge/context.py 一致的 conversation key）
            from hashlib import sha256
            from Agent import SessionManager
            from database.db_manager import get_db_manager

            raw = f"pinduoduo|{shop_id}|{user_id}|customer|{self.from_uid}"
            session_id = f"conversation_{sha256(raw.encode('utf-8')).hexdigest()}"

            session_mgr = SessionManager(session_factory=get_db_manager().Session)
            session_mgr.delete_session_messages(session_id)
            for msg in content_list:
                direction = msg.get("direction", False)
                session_mgr.add_message(
                    session_id=session_id,
                    role="assistant" if direction else "user",
                    content=msg.get("content", ""),
                    tool_call_id=msg.get("csname", ""),
                )
            success = True

            if not success:
                self.sync_failed.emit("保存到数据库失败")
                return

            # 更新内存缓存
            message_cache.set(shop_id, self.from_uid, content_list)

            # 发送成功信号
            user_info = result.get("user", {})
            self.sync_success.emit({"from_uid": self.from_uid, **user_info}, content_list)

        except Exception as e:
            self.logger.error(f"同步消息失败: error_type={type(e).__name__}:{e}")
            self.sync_failed.emit(str(e))

    def _get_anti_content_from_browser(self, username: str, password: str, shop_id: str, user_id: str) -> str:
        """通过 Playwright 浏览器获取 anti-content"""
        import asyncio
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            lambda: asyncio.run(self._async_get_anti_content(username, password, shop_id, user_id))
                        )
                        return future.result(timeout=60)
                else:
                    return loop.run_until_complete(
                        self._async_get_anti_content(username, password, shop_id, user_id)
                    )
            except RuntimeError:
                return asyncio.run(
                    self._async_get_anti_content(username, password, shop_id, user_id)
                )
        except Exception as e:
            self.logger.error(f"通过浏览器获取 anti-content 失败: {e}")
            return ""

    async def _async_get_anti_content(self, username: str, password: str, shop_id: str, user_id: str) -> str:
        """异步获取 anti-content"""
        from Channel.pinduoduo.pdd_login import PDDLogin
        profile_scope = f"pinduoduo_{shop_id}_{user_id}"
        pdd_login = PDDLogin(name=username, password=password, profile_scope=profile_scope)
        result = await pdd_login.get_anti_content()
        if result and result is not False:
            return result
        return ""


__all__ = ['LogoLoaderThread', 'AutoReplyThread', 'SetStatusThread', 'FetchCustomersThread', 'FetchMessagesThread', 'SyncMessagesThread']
