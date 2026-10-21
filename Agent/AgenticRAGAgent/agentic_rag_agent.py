"""
AgenticRAGAgent 带自反思的 Agentic RAG Agent
============================================

参照 UltimateRAG src/stage_3 实现。
继承 AgentBase,只实现业务差异钩子,样板代码全部由基类提供。

模块划分：
    agent_config.py  配置常量        schemas.py  结构化输出模型
    prompts.py       提示词模板      llm_client.py  ChatOpenAI 工厂
    router.py        智能路由        self_rag.py  自反思 RAG 闭环

整体流程（Agent Loop 模式）：
    1. _get_rag_context() → SelfRAGPipeline.retrieve_refined 精检索，提供初始上下文
    2. _get_system_prompt() → 系统提示词 + RAG 上下文
    3. _get_tools()     → 绑定工具（知识库搜索、商品查询、转人工等）
    4. _run_agent_loop() → ReAct 循环：LLM → 工具调用 → 回传 → 循环
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from Agent.AgentBase import AgentBase, EMPTY_REPLY, INIT_FAILED_REPLY
from Agent.llm_client import create_chat_model
from Agent.AgenticRAGAgent.prompts import AGENT_SYSTEM_PROMPT
from Agent.session_manager import SessionManager
from bridge.context import Context, ContextType, context_scope
from bridge.reply import Reply, ReplyType
from config import get_config
from core.di_container import container, injectable
from database.db_manager import get_db_manager
from database.knowledge_service import KnowledgeService
from utils.logger_loguru import get_logger
from Agent.tool_decorator import get_tools_for_llm

logger = get_logger("AgenticRAGAgent")


@injectable()
class AgenticRAGAgent(AgentBase):
    """带自反思的 Agentic RAG Agent

    用法：
        agent = AgenticRAGAgent()
        reply = await agent.ask("可以退货吗", shop_id=123)
    """

    def __init__(self, account_info=None):
        super().__init__()
        self.account_info = account_info
        self._llm = None
        # 延迟初始化的运行时组件
        self._knowledge_service: Optional[KnowledgeService] = None
        logger.info("AgenticRAGAgent 实例创建成功")

    # ==================================================================
    # AgentBase 钩子实现
    # ==================================================================

    async def _on_initialize(self) -> None:
        """创建知识库服务、会话管理器（SelfRAGPipeline 使用共享单例）"""
        self._llm = create_chat_model(temperature=get_config("llm.generate_temperature", 0.3))
        self._knowledge_service = container.get(KnowledgeService)
        self._session_manager = SessionManager(
            session_factory=get_db_manager().Session,
            model_name=get_config("llm.model_name", "gpt-3.5-turbo"),
        )
        self._max_loops = get_config("agent.max_loops", 10)

    async def _on_close(self) -> None:
        """清空子类自有组件（session_manager 由基类释放）"""
        self._knowledge_service = None

    # ==================================================================
    # Agent Loop 钩子：系统提示词 / 上下文 / 工具 / 依赖
    # ==================================================================

    def _get_system_prompt(self) -> str:
        """返回系统提示词（RAG 上下文由 _get_rag_context 提供，_build_messages 拼入）"""
        return AGENT_SYSTEM_PROMPT

    def _get_tools(self) -> list:
        """返回 Agent 可用工具列表（OpenAI tools 格式，复用 @agent_tool 注册表）"""
        return get_tools_for_llm()

    # ==================================================================
    # 独立测试入口（通道走 AgentBase.async_reply）
    # ==================================================================
    async def ask(self, question: str, shop_id: int, session_id: Optional[str] = None) -> Reply:
        """独立测试入口：构造带 shop_id 的 Context 后委托基类会话流程"""
        if not await self.initialize_async():
            return Reply(ReplyType.TEXT, INIT_FAILED_REPLY)
        sid = session_id or self._fallback_session_id
        lock = self._conversation_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            context = Context(
                type=ContextType.TEXT,
                content=question,
                kwargs={"shop_id": shop_id},
            ) if shop_id else None
            content = await self._handle_session(question, context, sid)
            return Reply(ReplyType.TEXT, content or EMPTY_REPLY)

