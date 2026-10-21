"""
MultiAgent 子 Agent 基类
========================

BaseSubAgent 继承 AgentBase，复用其完整的模板方法骨架：
  initialize_async → async_reply → _handle_session → _build_messages → _run_agent_loop

子类只需覆盖钩子方法定制行为（系统提示词、工具集），
不再重复实现会话管理、Agent Loop、历史压缩等逻辑。

共享的工具执行逻辑（@agent_tool 体系）也在此提供，
避免每个子 Agent 重复覆盖 _execute_tool。
"""
from __future__ import annotations

import asyncio
import contextvars
from abc import abstractmethod
from typing import Any, Dict, List, Optional

from Agent.AgentBase import (
    AgentBase,
    INIT_FAILED_REPLY,
    ERROR_REPLY,
    EMPTY_REPLY,
)
from Agent.llm_client import create_chat_model
from Agent.session_manager import SessionManager
from bridge.context import Context, context_scope
from bridge.reply import Reply, ReplyType
from config import get_config
from database.db_manager import get_db_manager
from utils.logger_loguru import get_logger

logger = get_logger("MultiAgent.sub_agent")

# Supervisor 注入的共享会话上下文，子 Agent 检测到时跳过自身历史管理
_shared_session_ctx: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "_shared_session_ctx", default=None,
)


class BaseSubAgent(AgentBase):
    """子 Agent 基类：继承 AgentBase，提供共享的工具执行和依赖注入

    子类只需覆盖：
      - _on_initialize()  → 创建 LLM + SessionManager
      - _get_system_prompt() → 返回系统提示词
      - _get_tools()      → 返回工具列表（OpenAI tools 格式）

    RAG 上下文：
      基类 AgentBase._get_rag_context 默认执行 SelfRAG 精检索，
      不需要 RAG 的子类（如 ChatAgent）覆盖返回空字符串即可。
    """

    # ------------------------------------------------------------------
    # 默认初始化：创建 LLM + SessionManager（子类通常无需覆盖）
    # ------------------------------------------------------------------

    async def _on_initialize(self) -> None:
        """默认初始化：创建 LLM 和 SessionManager

        子类只需覆盖 _get_system_prompt() / _get_tools() 等钩子，
        通常不需要覆盖此方法（ChatAgent 等特殊情况除外）。
        """
        self._llm = create_chat_model(temperature=get_config("llm.generate_temperature", 0.3))
        self._session_manager = SessionManager(
            session_factory=get_db_manager().Session,
            model_name=get_config("llm.model_name", "gpt-3.5-turbo"),
        )

    # ------------------------------------------------------------------
    # 委托执行入口：供 Supervisor 直接调用（跳过中间件管道 / 会话锁 / request_scope）
    # ------------------------------------------------------------------

    async def run_delegated(
        self,
        query: str,
        context: Optional[Context],
        session_id: str,
    ) -> Reply:
        """内部委托执行入口，专供 Supervisor 调度使用。

        与 async_reply 的区别：不再构建中间件管道、不加会话锁、不开新的
        request_scope、不计 _active_requests。原因：Supervisor 已在其
        _core_reply 中统一完成输入校验、日志、指标、超时、同会话串行与
        request_scope，子 Agent 重复这些会造成：
          - 指标重复计数、trace 链路被切成两段
          - 超时叠加（子 Agent 各自再套一层 request_timeout）
          - 冗余的第二套会话锁
        因此这里只保留“确保已初始化 + 执行会话处理 + 异常兜底”。

        并发约定：会话锁由 Supervisor 持有，本方法不加锁，
        调用方必须保证同一会话串行调用。
        """
        if self._is_shutting_down:
            return Reply(ReplyType.TEXT, "系统正在关闭，请稍后再试。")
        if not await self.initialize_async():
            return Reply(ReplyType.TEXT, INIT_FAILED_REPLY)
        try:
            content = await self._handle_session(query, context, session_id)
            return Reply(ReplyType.TEXT, content or EMPTY_REPLY)
        except Exception as e:
            logger.error(
                f"{type(self).__name__} 委托回复失败: error_type={type(e).__name__}:{e}"
            )
            return Reply(ReplyType.TEXT, ERROR_REPLY)

    # ------------------------------------------------------------------
    # 会话处理：检测共享会话上下文，有则走 Supervisor 托管流程
    # ------------------------------------------------------------------

    async def _handle_session(self, query: str, context: Optional[Context], session_id: str) -> str:
        """会话处理入口：若 Supervisor 注入了共享会话上下文，则走共享流程"""
        shared = _shared_session_ctx.get()
        if shared is not None:
            return await self._handle_shared_session(query, context, shared, session_id)
        return await super()._handle_session(query, context, session_id)

    async def _handle_shared_session(
        self,
        query: str,
        context: Optional[Context],
        shared: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> str:
        """共享会话流程：使用 Supervisor 注入的历史，跳过自身历史管理

        Supervisor 已负责：
          - 共享历史的加载与压缩
          - 用户消息与最终文本回复的持久化
        本子 Agent 只需：
          - 用共享历史构建消息 → 执行 Agent Loop → 返回结果

        传入 session_id 后，Agent Loop 会把本轮的工具调用轨迹
        （assistant.tool_calls + tool 结果）持久化到共享历史，
        使后续轮次能看到“上一轮已调用过哪些工具”，避免重复调用。
        注意：最终文本回复仍由 Supervisor 落库，此处不重复持久化。
        """
        history = shared.get("history", [])
        # 优先使用 Supervisor 注入的 session_id，兜底用参数传入的
        shared_session_id = shared.get("session_id") or session_id
        messages = await self._build_messages(query, history, context)
        answer = await self._run_agent_loop(
            messages, context, session_id=shared_session_id,
        )
        return answer or ""


