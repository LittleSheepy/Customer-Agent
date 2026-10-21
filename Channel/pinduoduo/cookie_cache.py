"""
共享内存级缓存

包含:
- CookieCache: 线程安全的内存 Cookie 缓存，按 (channel_name, shop_id, user_id) 索引
- AntiContentCache: 线程安全的内存 anti-content 缓存，按 (shop_id, user_id) 索引
- MessageCache: 线程安全的内存消息缓存，按 (shop_id, from_uid) 索引

所有 BaseRequest 实例通过此缓存共享 cookie，确保一处更新全局生效。
线程安全设计：使用 threading.RLock 保护所有读写操作。
"""

import threading
import time
from typing import Dict, List, Optional


class CookieCache:
    """线程安全的内存 Cookie 缓存，按 (channel_name, shop_id, user_id) 索引"""

    def __init__(self):
        self._lock = threading.RLock()
        self._cache: Dict[str, dict] = {}
        self._timestamps: Dict[str, float] = {}

    @staticmethod
    def _make_key(channel_name: str, shop_id: str, user_id: str) -> str:
        return f"{channel_name}:{shop_id}:{user_id}"

    def get(self, channel_name: str, shop_id: str, user_id: str) -> Optional[dict]:
        """从缓存获取 cookie，未命中返回 None"""
        key = self._make_key(channel_name, shop_id, user_id)
        with self._lock:
            return self._cache.get(key)

    def set(self, channel_name: str, shop_id: str, user_id: str, cookies: dict):
        """更新缓存中的 cookie"""
        key = self._make_key(channel_name, shop_id, user_id)
        with self._lock:
            self._cache[key] = cookies
            self._timestamps[key] = time.time()

    def invalidate(self, channel_name: str, shop_id: str, user_id: str):
        """移除缓存条目"""
        key = self._make_key(channel_name, shop_id, user_id)
        with self._lock:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)

    def clear(self):
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()

    def get_age(self, channel_name: str, shop_id: str, user_id: str) -> Optional[float]:
        """获取缓存条目的年龄（秒），未命中返回 None"""
        key = self._make_key(channel_name, shop_id, user_id)
        with self._lock:
            ts = self._timestamps.get(key)
            if ts is None:
                return None
            return time.time() - ts


class AntiContentCache:
    """线程安全的内存 anti-content 缓存，按 (shop_id, user_id) 索引
    
    anti-content 有效期较短，默认缓存 5 分钟后过期。
    """

    # 默认缓存时间：5 分钟（anti-content 有效期较短）
    DEFAULT_TTL = 300

    def __init__(self):
        self._lock = threading.RLock()
        self._cache: Dict[str, str] = {}
        self._timestamps: Dict[str, float] = {}
        self._ttl = self.DEFAULT_TTL

    @staticmethod
    def _make_key(shop_id: str, user_id: str) -> str:
        return f"anti:{shop_id}:{user_id}"

    def get(self, shop_id: str, user_id: str) -> Optional[str]:
        """从缓存获取 anti-content，过期或未命中返回 None"""
        key = self._make_key(shop_id, user_id)
        with self._lock:
            ts = self._timestamps.get(key)
            if ts is None:
                return None
            # 检查是否过期
            if time.time() - ts > self._ttl:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
                return None
            return self._cache.get(key)

    def set(self, shop_id: str, user_id: str, anti_content: str):
        """更新缓存中的 anti-content"""
        key = self._make_key(shop_id, user_id)
        with self._lock:
            self._cache[key] = anti_content
            self._timestamps[key] = time.time()

    def invalidate(self, shop_id: str, user_id: str):
        """移除缓存条目"""
        key = self._make_key(shop_id, user_id)
        with self._lock:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)

    def clear(self):
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()


class MessageCache:
    """线程安全的内存消息缓存，按 (shop_id, from_uid) 索引
    
    缓存客户消息历史，默认 10 分钟过期。
    """

    # 默认缓存时间：10 分钟
    DEFAULT_TTL = 600

    def __init__(self):
        self._lock = threading.RLock()
        self._cache: Dict[str, List[dict]] = {}
        self._timestamps: Dict[str, float] = {}
        self._ttl = self.DEFAULT_TTL

    @staticmethod
    def _make_key(shop_id: str, from_uid: str) -> str:
        return f"msg:{shop_id}:{from_uid}"

    def get(self, shop_id: str, from_uid: str) -> Optional[List[dict]]:
        """从缓存获取消息列表，过期或未命中返回 None"""
        key = self._make_key(shop_id, from_uid)
        with self._lock:
            ts = self._timestamps.get(key)
            if ts is None:
                return None
            # 检查是否过期
            if time.time() - ts > self._ttl:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
                return None
            return self._cache.get(key)

    def set(self, shop_id: str, from_uid: str, messages: List[dict]):
        """更新缓存中的消息列表"""
        key = self._make_key(shop_id, from_uid)
        with self._lock:
            self._cache[key] = messages
            self._timestamps[key] = time.time()

    def invalidate(self, shop_id: str, from_uid: str):
        """移除缓存条目"""
        key = self._make_key(shop_id, from_uid)
        with self._lock:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)

    def clear(self):
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()


# 模块级单例
cookie_cache = CookieCache()
anti_content_cache = AntiContentCache()
message_cache = MessageCache()
