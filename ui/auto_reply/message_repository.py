"""
自动回复模块 - 聊天消息数据访问层（Repository）
================================================

将 UI 层与数据库访问解耦：AutoReplyUI 只通过 MessageRepository
读写消息历史，不再直接依赖 SessionManager / db_manager / 会话 key 规则。

职责边界：
- 只负责 agent_messages 表的读、写、追加
- 负责 PDD 消息格式 <-> agent_messages 存储格式的转换
- 不涉及任何 UI 组件（可独立单元测试）
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from bridge.context import make_conversation_key_raw
from utils.logger_loguru import get_logger

logger = get_logger("MessageRepository")


class MessageRepository:
    """聊天消息持久化仓储：隔离 UI 与数据库"""

    def __init__(self):
        # 懒加载：保持与原 _get_session_mgr 相同的"首次使用时创建"行为，
        # 避免 UI 构造早于数据库就绪时的初始化顺序问题
        self._session_mgr = None

    @property
    def session_mgr(self):
        """SessionManager 复用单例（构造含 tiktoken 估算器，成本较高）

        Agent 包的 __init__ 会拉起 LangChain / RAG / chromadb 等重型依赖，
        故在此延迟导入，避免 import 本模块（及 ui.py）时就加载整个 Agent 工具链。
        类型注解依赖顶部的 from __future__ import annotations，无需模块级导入。
        """
        if self._session_mgr is None:
            from Agent import SessionManager
            from database.db_manager import get_db_manager
            self._session_mgr = SessionManager(session_factory=get_db_manager().Session)
        return self._session_mgr

    @staticmethod
    def _conversation_key(shop_id: str, from_uid: str, user_id: str = "") -> str:
        """生成会话 key，复用 bridge/context.py 的统一实现"""
        return make_conversation_key_raw("pinduoduo", shop_id, user_id, from_uid)

    def load_messages(self, shop_id: str, from_uid: str, user_id: str = "") -> list:
        """从 agent_messages 表加载消息历史（返回 PDD 格式）"""
        try:
            session_id = self._conversation_key(shop_id, from_uid, user_id)
            agent_messages = self.session_mgr.get_history(session_id)
            result = []
            for msg in agent_messages:
                ts = msg.get("timestamp")
                if isinstance(ts, datetime):
                    ts = int(ts.timestamp())
                elif isinstance(ts, str):
                    try:
                        ts = int(datetime.fromisoformat(ts).timestamp())
                    except Exception:
                        ts = 0
                result.append({
                    "content": msg.get("content", ""),
                    "direction": msg.get("role") == "assistant",
                    "csname": msg.get("tool_call_id", ""),
                    "ts": ts,
                })
            return result
        except Exception as e:
            logger.error(f"从数据库加载消息失败: {e}")
            return []

    def save_messages(self, shop_id: str, from_uid: str, messages: list, user_id: str = ""):
        """保存 PDD 消息列表到 agent_messages 表（先删后插）"""
        try:
            session_id = self._conversation_key(shop_id, from_uid, user_id)
            self.session_mgr.delete_session_messages(session_id)
            for msg in messages:
                direction = msg.get("direction", False)
                self.session_mgr.add_message(
                    session_id=session_id,
                    role="assistant" if direction else "user",
                    content=msg.get("content", ""),
                    tool_call_id=msg.get("csname", ""),
                )
            logger.info(f"成功保存 {len(messages)} 条消息到数据库")
        except Exception as e:
            logger.error(f"保存消息到数据库失败: {e}")

    def append_message(self, shop_id: str, from_uid: str, content: str,
                       is_self: bool, csname: str, ts: int, user_id: str = ""):
        """追加单条消息到 agent_messages 表"""
        try:
            session_id = self._conversation_key(shop_id, from_uid, user_id)
            self.session_mgr.add_message(
                session_id=session_id,
                role="assistant" if is_self else "user",
                content=content,
                tool_call_id=csname,
            )
        except Exception as e:
            logger.error(f"追加消息到数据库失败: {e}")


__all__ = ["MessageRepository"]
