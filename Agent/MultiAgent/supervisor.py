"""
MultiAgent Supervisor Agent（调度中枢）
=======================================

通过 QueryClassifier 识别用户意图，分发到对应子 Agent 处理。
继承 AgentBase 复用初始化锁、中间件管道等基础设施，
重写 _handle_session 实现分类调度与共享会话历史管理，
会话锁、请求计数、初始化守卫与异常兜底均由基类 _core_reply 统一提供。

子 Agent 继承 AgentBase（通过 BaseSubAgent），
复用完整的 Agent Loop、历史压缩等能力。
Supervisor 统一管理所有子 Agent 的会话历史，
通过 ContextVar 注入共享上下文，避免意图切换时用户重复描述。

调度流程：
    1. 接收用户消息（经基类中间件管道 + 会话锁：校验 → 日志 → 指标 → 超时 → 同会话串行）
    2. QueryClassifier 识别意图（product_inquiry / after_sales / chat）
    3. 加载 / 压缩共享历史，持久化用户消息
    4. 通过 ContextVar 注入共享会话，委托子 Agent
    5. 持久化子 Agent 回复到共享历史
"""
from __future__ import annotations

from typing import Dict, List, Optional

from Agent.AgentBase import AgentBase, ERROR_REPLY
from Agent.llm_client import create_chat_model
from Agent.session_manager import SessionManager
from Agent.MultiAgent.router import QueryClassifier
from Agent.MultiAgent.SubAgent import (
    BaseSubAgent,
    ProductAgent,
    AfterSalesAgent,
    ChatAgent,
)
from Agent.MultiAgent.SubAgent.base import _shared_session_ctx
from bridge.context import Context
from bridge.reply import ReplyType
from config import get_config
from core.di_container import injectable
from database.db_manager import get_db_manager
from utils.logger_loguru import get_logger
from utils.observability import metrics, get_request_context

logger = get_logger("SupervisorAgent")



@injectable()
class SupervisorAgent(AgentBase):
    """Supervisor Agent：多智能体调度中枢"""

    def __init__(self, account_info=None):
        super().__init__()
        self.account_info = account_info
        self._classifier: Optional[QueryClassifier] = None
        self._sub_agents: Dict[str, BaseSubAgent] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def _on_initialize(self) -> None:
        """创建意图分类器和各子 Agent（子 Agent 延迟初始化，首次 run_delegated 时触发）"""
        self._llm = create_chat_model(temperature=get_config("llm.generate_temperature", 0.3))
        self._classifier = QueryClassifier()

        # 注册子 Agent 实例（不在此处 initialize，由 run_delegated 首次调用时自动初始化）
        for agent in [ProductAgent(), AfterSalesAgent(), ChatAgent()]:
            self._sub_agents[type(agent).__name__] = agent

        # 基类需要 _session_manager（用于 close 等通用流程）
        self._session_manager = SessionManager(
            session_factory=get_db_manager().Session,
            model_name=get_config("llm.model_name", "gpt-3.5-turbo"),
        )
        self._max_loops = get_config("agent.max_loops", 10)
        logger.info(f"SupervisorAgent 初始化完成，已注册子 Agent: {list(self._sub_agents.keys())}")

    async def _on_close(self) -> None:
        """关闭所有子 Agent"""
        for agent in self._sub_agents.values():
            await agent.close()
        self._sub_agents.clear()
        self._classifier = None

    # ------------------------------------------------------------------
    # 会话处理（重写基类 _handle_session 以实现分类调度 + 共享历史管理）
    # 入口走基类 async_reply → 中间件管道 → _core_reply（会话锁 / 计数 /
    # 初始化守卫 / 异常兜底）→ 此处，只负责业务调度，返回文本内容。
    # ------------------------------------------------------------------

    async def _handle_session(
        self,
        query: str,
        context: Optional[Context],
        session_id: str,
    ) -> str:
        """会话级处理逻辑：意图分类 → 共享历史管理 → 委托子 Agent

        由基类 _core_reply 在持有会话锁、完成初始化守卫后调用，
        因此这里无需再处理并发串行、初始化、请求计数与异常兜底，
        只专注调度流程本身，返回子 Agent 的文本回复。
        """
        # 1. 意图分类（request_scope 已由基类 _core_reply 打开）
        # 传入 session_id 以启用会话级意图缓存（降本），缓存内含漂移熔断
        agent_name, classification = await self._classify_intent(query, session_id)
        ctx = get_request_context()
        if ctx is not None:
            ctx.intent = classification.intent.value
        metrics.inc(f"intent.{classification.intent.value}")
        metrics.observe("intent.confidence", classification.confidence)
        # 置信度过低时回退到通用 Agent，避免把不确定的意图硬分给专用子 Agent
        agent_name = self._apply_confidence_policy(agent_name, classification)
        logger.info(
            f"Supervisor 调度: intent={classification.intent.value} "
            f"→ agent={agent_name} (confidence={classification.confidence:.2f})"
        )

        # 2. 获取子 Agent
        sub_agent = self._sub_agents.get(agent_name)
        if sub_agent is None:
            logger.warning(f"子 Agent 不存在: {agent_name}，回退到 ProductAgent")
            sub_agent = self._sub_agents["ProductAgent"]

        # 3. 管理共享会话历史（所有子 Agent 共用同一份，session_id 由基类解析并传入）
        # 3.1 加载共享历史，超阈值时压缩
        history = await self._load_and_compress_history(session_id)

        # 3.2 持久化用户消息到共享历史
        await self._db(
            self._session_manager.add_message,
            session_id=session_id, role="user", content=query,
        )

        # 4. 注入共享会话上下文，委托子 Agent
        # 走 run_delegated 内部入口：Supervisor 已完成中间件管道 / 会话锁 /
        # request_scope，子 Agent 无需重复，避免指标重复计数、超时叠加与 trace 断裂。
        # 同时透传 session_id，使子 Agent 的工具调用轨迹落入共享历史。
        token = _shared_session_ctx.set({"session_id": session_id, "history": history})
        try:
            reply = await sub_agent.run_delegated(query, context, session_id)
        finally:
            _shared_session_ctx.reset(token)

        # 5. 提取子 Agent 文本回复（前提：子 Agent 仅返回 TEXT 类型）
        content = reply.content if reply.type == ReplyType.TEXT else ""

        # 6. 持久化子 Agent 回复到共享历史（空回复 / 错误兜底不落库）
        if content and content != ERROR_REPLY:
            await self._db(
                self._session_manager.add_message,
                session_id=session_id, role="assistant", content=content,
            )

        return content or ""

    # ------------------------------------------------------------------
    # 意图分类
    # ------------------------------------------------------------------

    async def _classify_intent(self, query: str, session_id: Optional[str] = None):
        """分类用户意图，返回 (agent_name, IntentClassification)"""
        logger.info(f"开始分类用户意图==>{query}")
        return await self._classifier.decide_agent(query, session_id)

    def _apply_confidence_policy(self, agent_name: str, classification) -> str:
        """置信度策略：分类置信度过低时回退到通用 Agent。

        低置信度意味着分类器对意图不确定，此时硬分给专用子 Agent（如售后）
        容易误路由。回退到工具最全的通用 Agent（默认 ProductAgent，含 RAG +
        商品知识 + 商品列表），由其自行判断，比误路由更稳妥。

        阈值与回退目标均可配置：
          - agent.classify_min_confidence（默认 0.6）
          - agent.classify_fallback_agent（默认 ProductAgent）
        """
        min_conf = get_config("agent.classify_min_confidence", 0.6)
        if classification.confidence >= min_conf:
            return agent_name
        fallback = get_config("agent.classify_fallback_agent", "ProductAgent")
        if fallback not in self._sub_agents:
            fallback = "ProductAgent"
        metrics.inc("intent.low_confidence")
        logger.warning(
            f"分类置信度过低 ({classification.confidence:.2f} < {min_conf})，"
            f"由 {agent_name} 回退到通用 Agent: {fallback}"
        )
        return fallback

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    @property
    def sub_agent_names(self) -> List[str]:
        """已注册的子 Agent 名称列表"""
        return list(self._sub_agents.keys())
