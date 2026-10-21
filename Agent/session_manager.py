"""
会话历史管理模块

提供 Agent 会话历史的 SQLite 持久化和上下文压缩功能。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.exc import SQLAlchemyError

from database.models import AgentMessage
from utils.logger_loguru import get_logger

logger = get_logger("SessionManager")


# ==============================================================================
# Token 估算
# ==============================================================================

class TokenEstimator:
    """Token 数量估算器"""

    def __init__(self, model_name: str = "gpt-4"):
        self._encoding = None
        self._available = False
        self._model_name = model_name
        self._init_encoding()

    def _init_encoding(self) -> None:
        try:
            import tiktoken
            self._encoding = tiktoken.encoding_for_model(self._model_name)
            self._available = True
            logger.debug("tiktoken 初始化成功")
        except ImportError:
            logger.warning("tiktoken 未安装，使用字符数估算 tokens")
            self._available = False
        except Exception:
            try:
                import tiktoken
                self._encoding = tiktoken.get_encoding("cl100k_base")
                self._available = True
                logger.debug("tiktoken cl100k_base 编码器初始化成功")
            except Exception as e:
                logger.warning(
                    f"tiktoken 编码器初始化失败: error_type={type(e).__name__}"
                )
                self._available = False

    def estimate(self, text: str) -> int:
        """估算文本的 token 数量"""
        if not text:
            return 0
        if self._available and self._encoding:
            return len(self._encoding.encode(text))
        # 降级：字符数估算（中文字符 ≈ 2 tokens，英文 ≈ 0.25 tokens/字符）
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 2 + other_chars * 0.25)

    def estimate_messages(self, messages: List[Dict[str, Any]]) -> int:
        """估算消息列表的总 token 数量"""
        total = 0
        for msg in messages:
            # 每个消息有 role overhead
            total += 4  # approximate per-message overhead
            if msg.get("content"):
                total += self.estimate(str(msg["content"]))
            if msg.get("tool_calls"):
                total += self.estimate(str(msg["tool_calls"]))
        return total


# ==============================================================================
# SessionManager
# ==============================================================================

class SessionManager:
    """
    会话历史管理器

    提供：
    - 消息持久化（SQLite）
    - 历史加载和追加
    - 基于 token_window 的上下文压缩

    不管理数据库连接，由外部传入 session_factory（来自 DatabaseManager）。
    """

    def __init__(
        self,
        session_factory: Callable,
        token_window: int = 131072,
        compress_ratio: float = 0.7,
        retain_count: int = 10,
        model_name: str = "gpt-4",
    ):
        """
        Args:
            session_factory: SQLAlchemy sessionmaker 实例（由 DatabaseManager 提供）
            token_window: 模型 context window 大小（默认 128k = 131072）
            compress_ratio: 压缩触发比例（默认 0.7，即超过 70% 时触发）
            retain_count: 压缩后保留的最近消息条数
            model_name: 用于 tiktoken 的模型名称
        """
        self._Session = session_factory
        self.token_window = token_window
        self.compress_ratio = compress_ratio
        self.retain_count = retain_count
        self.threshold = int(token_window * compress_ratio)

        self._token_estimator = TokenEstimator(model_name=model_name)
        logger.info(f"SessionManager 初始化完成: threshold={self.threshold}, retain={retain_count}")

    def _session(self):
        return self._Session()

    def add_message(self, session_id: str, role: str, content: str,
                    tool_call_id: Optional[str] = None) -> bool:
        """追加消息到会话历史"""
        if role not in {"user", "assistant", "tool", "summary"}:
            logger.warning(
                f"unknown session role rejected: role_type={type(role).__name__}"
            )
            return False
        session = self._session()
        try:
            msg = AgentMessage(
                session_id=session_id,
                role=role,
                content=content,
                tool_call_id=tool_call_id,
                timestamp=datetime.now(),
            )
            session.add(msg)
            session.commit()
            return True
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"添加消息失败: error_type={type(e).__name__}")
            return False
        finally:
            session.close()

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        """获取会话历史消息（按时间升序）"""
        session = self._session()
        try:
            messages = session.query(AgentMessage).filter(
                AgentMessage.session_id == session_id
            ).order_by(AgentMessage.timestamp.asc(), AgentMessage.id.asc()).all()
            return [msg.to_dict() for msg in messages]
        except SQLAlchemyError as e:
            logger.error(f"获取历史消息失败: error_type={type(e).__name__}")
            return []
        finally:
            session.close()

    def get_message_count(self, session_id: str) -> int:
        """获取会话消息总数"""
        session = self._session()
        try:
            return session.query(AgentMessage).filter(
                AgentMessage.session_id == session_id
            ).count()
        except SQLAlchemyError as e:
            logger.error(f"获取消息数量失败: error_type={type(e).__name__}")
            return 0
        finally:
            session.close()

    def delete_session_messages(self, session_id: str) -> bool:
        """删除指定 session_id 的所有消息"""
        session = self._session()
        try:
            session.query(AgentMessage).filter(
                AgentMessage.session_id == session_id
            ).delete()
            session.commit()
            return True
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"删除会话消息失败: error_type={type(e).__name__}")
            return False
        finally:
            session.close()

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """估算消息列表的 token 数量"""
        return self._token_estimator.estimate_messages(messages)

    def should_compress(self, session_id: str) -> Tuple[bool, List[Dict[str, Any]]]:
        """检查是否需要压缩。

        同时判断两个条件：
        1. token 数超过阈值
        2. 消息条数 > retain_count + 1（至少有 1 条可压缩）

        Returns:
            (是否需要压缩, 会话历史消息列表)
        """
        messages = self.get_history(session_id)
        if not messages:
            return False, messages
        token_count = self.estimate_tokens(messages)
        need_compress = (
            token_count > self.threshold
            and len(messages) > self.retain_count + 1
        )
        return need_compress, messages

    async def compress_history(self, session_id: str, llm_callable) -> bool:
        """
        压缩会话历史：保留最近 N 条 + 生成摘要

        Args:
            session_id: 会话 ID
            llm_callable: 接受 messages 列表，返回摘要字符串的可调用对象

        Returns:
            压缩是否成功
        """
        session = self._session()
        try:
            # 1. 获取全部历史
            messages = session.query(AgentMessage).filter(
                AgentMessage.session_id == session_id
            ).order_by(AgentMessage.timestamp.asc(), AgentMessage.id.asc()).all()

            if len(messages) <= self.retain_count + 1:
                logger.debug(f"消息数量 {len(messages)} 未达到压缩阈值，跳过")
                return False

            # 2. 分离要压缩的旧消息和要保留的新消息
            # 切分点做“边界吸附”：工具调用组（assistant(tool_calls) + 其后紧跟的 tool 结果）
            # 不能被拆开，否则保留窗口会以孤儿 tool 消息开头，触发 LLM
            # “tool 消息必须紧跟带 tool_calls 的 assistant” 校验错误。
            # 若切分点落在 tool 消息上，向前回退至该组的起点（assistant 或非 tool 消息）。
            split = len(messages) - self.retain_count
            while split > 0 and messages[split].role == "tool":
                split -= 1
            if split <= 0:
                logger.debug(f"边界吸附后无可压缩的旧消息，跳过: session={session_id}")
                return False
            old_messages = messages[:split]
            retain_messages = messages[split:]

            # 3. 生成摘要
            summary_input = [
                {
                    "role": msg.role,
                    "content": msg.content,
                }
                for msg in old_messages
            ]

            try:
                summary = await llm_callable(summary_input)
            except Exception as e:
                logger.error(f"生成摘要失败: error_type={type(e).__name__}")
                # Never delete history when the summary is unavailable.
                session.rollback()
                return False

            # 4. 删除旧消息
            for msg in old_messages:
                session.delete(msg)

            # 5. 在顶部插入不可信摘要消息
            summary_timestamp = retain_messages[0].timestamp - timedelta(microseconds=1)
            summary_msg = AgentMessage(
                session_id=session_id,
                # 生成的模型摘要是对话数据，绝非新的系统指令。MessageBuilder 为这一角色配备了用户级别的安全防护层。
                role="summary",
                content=str(summary),
                tool_call_id=None,
                timestamp=summary_timestamp,
            )
            session.add(summary_msg)

            # 6. 更新保留消息的时间戳（保持顺序）
            # 保留消息已经在数据库中，无需移动

            session.commit()
            logger.info(
                f"压缩完成: session={session_id}, "
                f"删除={len(old_messages)} 条, 保留={len(retain_messages)} 条"
            )
            return True

        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"压缩历史失败: error_type={type(e).__name__}")
            return False
        finally:
            session.close()
