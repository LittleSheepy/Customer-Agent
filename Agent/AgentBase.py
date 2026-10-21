"""
AgentBase 客服 Agent 通用基类
=============================

抽取各 Agent 子类的公共骨架，
子类只实现业务差异，不再重复编写初始化锁、会话串行、
历史加载/压缩/持久化等样板代码。

【模板方法模式】基类已固化的流程
1. initialize_async()  双重检查锁，只初始化一次
2. async_reply()       同一买家会话串行处理 + 统一异常兜底
3. _handle_session()   加载历史 -> 必要时压缩 -> 持久化用户消息
                       -> _build_messages -> _run_agent_loop
                       -> 持久化最终回复
4. close()             锁内清空引用，锁外释放会话管理器连接

【ReAct Agent Loop】
_handle_session 不再调用 _handle_message，而是：
  1. _build_messages()  构建消息列表（system prompt + 历史 + context + 用户消息）
  2. _run_agent_loop()  ReAct 循环：LLM → tool_calls → 执行工具 → 回传 → 循环

【子类必须/可选实现的钩子】
- async _on_initialize() -> None           【必须】创建各组件；
        必须给 self._session_manager 赋值；失败直接抛异常
- str _get_system_prompt()                 【可选】系统提示词，默认空
- list _get_tools()                        【可选]LangChain BaseTool 列表，绑定到 LLM
- async _get_rag_context(query, history, ctx) 【可选】RAG 检索结果，注入系统提示词
- dict _get_tool_dependencies(context)     【可选】工具执行时的可信依赖（shop_id 等）
- async _on_close() -> None                【可选】关闭子类自有组件
- async _summarize_text(content) -> str    【需要历史压缩时必须实现】
"""
from __future__ import annotations

import json

import asyncio
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage

from Agent.middleware import (
    MiddlewarePipeline,
    input_validation_middleware,
    logging_middleware,
    metrics_middleware,
    timeout_middleware,
)
from Agent.session_manager import SessionManager
from Agent.message_builder import MessageBuilder
from Agent.utils import messages2content, format_documents, to_langchain_messages
from Agent.tools.get_product_list import get_shop_products, GetShopProductsParams
from Agent.prompts_utils import (
    CATALOG_WRAPPER_PREFIX, CATALOG_WRAPPER_SUFFIX,
    SESSION_INFO_FIELDS, SESSION_INFO_HEADER, SESSION_INFO_FOOTER,
    PRODUCT_LIST_NOTE,
)
from bridge.context import Context, _context_value, context_scope, make_conversation_key
from bridge.reply import Reply, ReplyType
from config import get_config
from database.db_manager import get_db_manager
from utils.logger_loguru import get_logger
from utils.observability import metrics, request_scope

logger = get_logger("AgentBase")

# 统一兜底话术（子类可用同名类属性覆盖）
INIT_FAILED_REPLY = "AI客服初始化失败，请检查配置。"
ERROR_REPLY = "抱歉，我现在无法回复，请稍后再试。"
EMPTY_REPLY = "抱歉，我暂时无法回复。"


class AgentBase:
    """客服 Agent 通用基类（模板方法模式 + ReAct Agent Loop）"""

    def __init__(self):
        super().__init__()
        # LLM 实例（LangChain ChatModel，需支持 bind_tools / ainvoke）
        self._llm = None
        # 初始化状态与双重检查锁 保证并发消息只初始化一次
        self._is_initialized = False
        self._initialize_lock = asyncio.Lock()
        # 每个买家会话一把锁（OrderedDict + LRU 淘汰，防长期运行内存泄漏）
        self._conversation_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._max_session_locks: int = get_config("agent.max_session_locks", 1000)
        # 优雅关闭状态
        self._is_shutting_down = False
        self._active_requests = 0
        # 无买家身份时的兜底会话 ID（进程内固定，避免历史丢失）
        self._fallback_session_id = f"fallback_{uuid.uuid4().hex}"
        # 会话管理器，由子类在 _on_initialize 中创建
        self._session_manager: Optional[SessionManager] = None
        # 消息构建器（子类可在 _on_initialize 中创建，否则 _build_messages 使用简单默认实现）
        self._message_builder: Optional[MessageBuilder] = None
        # Agent Loop 最大轮次（子类可覆盖）
        self._max_loops: int = get_config("agent.max_loops", 10)

    # ==================================================================
    # 1. 生命周期：初始化（模板 + 钩子）
    # ==================================================================

    async def initialize_async(self) -> bool:
        """延迟初始化，并发首条消息也只初始化一次"""
        if self._is_initialized:
            return True
        async with self._initialize_lock:
            if self._is_initialized:
                return True

            try:
                await self._on_initialize()
            except Exception as e:
                logger.error(f"{type(self).__name__} 初始化失败: error_type={type(e).__name__}:{e}")
                return False

            if self._session_manager is None:
                logger.error(f"{type(self).__name__} 初始化失败: _on_initialize 未设置 _session_manager")
                return False

            # 子类未创建 MessageBuilder 时，用 _get_system_prompt 创建默认实例
            if self._message_builder is None:
                self._message_builder = MessageBuilder(
                    system_prompt=self._get_system_prompt(),
                    session_info_fields=SESSION_INFO_FIELDS,
                    session_info_header=SESSION_INFO_HEADER,
                    session_info_footer=SESSION_INFO_FOOTER,
                    catalog_key="product_list",
                    catalog_wrapper=(CATALOG_WRAPPER_PREFIX, CATALOG_WRAPPER_SUFFIX),
                    business_hours=get_config("business_hours", {"start": "08:00", "end": "23:00"}),
                )

            self._is_initialized = True
            logger.info(f"{type(self).__name__} 初始化成功")
            return True

    async def _on_initialize(self) -> None:
        """【子类必须实现】创建 LLM、会话管理器等组件。

        约定：
        - 必须给 self._session_manager 赋值
        - 配置校验不通过或组件创建失败时直接抛异常
        """
        raise NotImplementedError("子类必须实现 _on_initialize")

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """获取会话锁，LRU 淘汰最久不用的锁，防内存无限增长"""
        if session_id in self._conversation_locks:
            self._conversation_locks.move_to_end(session_id)
            return self._conversation_locks[session_id]
        # 淘汰最久不用的锁
        if len(self._conversation_locks) >= self._max_session_locks:
            evicted_id, _ = self._conversation_locks.popitem(last=False)
            logger.info(f"LRU 淘汰会话锁: session={evicted_id[:16]}...")
        lock = asyncio.Lock()
        self._conversation_locks[session_id] = lock
        return lock

    # ==================================================================
    # 2. 生命周期：资源释放（模板 + 钩子）
    # ==================================================================

    async def close(self) -> None:
        """释放 Agent 占有的 LLM、数据库等资源，可重复调用"""
        self._is_shutting_down = True
        async with self._initialize_lock:
            session_manager = self._session_manager
            self._session_manager = None
            self._message_builder = None
            self._is_initialized = False
            self._conversation_locks.clear()
            try:
                # 子类自有组件（LLM 客户端等）在此关闭/清空
                await self._on_close()
            except Exception as e:
                logger.warning(f"{type(self).__name__} 自定义资源清理失败: error_type={type(e).__name__}")

        # session_manager 的连接池在锁外释放，避免长时间持锁
        if session_manager is not None:
            try:
                await asyncio.to_thread(session_manager.dispose)
            except Exception as e:
                logger.warning(
                    f"会话管理器清理失败: error_type={type(e).__name__}"
                )

    async def _on_close(self) -> None:
        """【子类可选实现】关闭/清空子类自有组件，基类已负责 session_manager"""

    # ==================================================================
    # 2.5 优雅关闭
    # ==================================================================

    async def graceful_shutdown(self, timeout: float = 30.0) -> None:
        """优雅关闭：拒绝新请求，等待进行中请求完成，然后释放资源

        Args:
            timeout: 等待进行中请求的最大秒数，超时后强制关闭
        """
        logger.info(f"{type(self).__name__} 开始优雅关闭（超时 {timeout}s）")
        self._is_shutting_down = True

        # 等待进行中的请求完成
        waited = 0.0
        while self._active_requests > 0 and waited < timeout:
            await asyncio.sleep(0.5)
            waited += 0.5
            logger.info(f"等待 {self._active_requests} 个进行中请求完成... ({waited:.1f}s)")

        if self._active_requests > 0:
            logger.warning(f"优雅关闭超时，仍有 {self._active_requests} 个请求未完成")

        await self.close()
        logger.info(f"{type(self).__name__} 关闭完成")

    # ==================================================================
    # 3. 通道入口：中间件管道 + 会话串行 + 统一异常兜底
    # ==================================================================

    async def async_reply(self, query: str, context: Optional[Context] = None) -> Reply:
        """消息通道入口：通过中间件管道处理"""
        if self._is_shutting_down:
            return Reply(ReplyType.TEXT, "系统正在关闭，请稍后再试。")
        pipeline = self._build_middleware_pipeline()
        return await pipeline.execute(query, context)

    def _build_middlewares(self) -> list:
        """【子类可覆盖】返回中间件列表，按顺序注册到管道

        默认：输入校验 → 日志 → 指标 → 超时
        """
        timeout_seconds = get_config("agent.request_timeout", 180.0)
        return [
            input_validation_middleware,
            logging_middleware,
            metrics_middleware,
            timeout_middleware(timeout_seconds=timeout_seconds),
        ]

    def _build_middleware_pipeline(self) -> MiddlewarePipeline:
        """构建中间件管道，核心处理为 _core_reply"""
        pipeline = MiddlewarePipeline(handler=self._core_reply)
        for mw in self._build_middlewares():
            pipeline.use(mw)
        return pipeline

    async def _core_reply(self, query: str, context: Optional[Context] = None) -> Reply:
        """核心回复逻辑（管道末端）：会话串行 + 初始化 + 处理"""
        self._active_requests += 1
        try:
            async with request_scope(agent=type(self).__name__) as ctx:
                session_id = self._resolve_session_id(context)
                ctx.session_id = session_id
                lock = self._get_session_lock(session_id)
                async with lock:
                    if not await self.initialize_async():
                        return Reply(ReplyType.TEXT, INIT_FAILED_REPLY)
                    try:
                        content = await self._handle_session(query, context, session_id)
                        return Reply(ReplyType.TEXT, content or EMPTY_REPLY)
                    except Exception as e:
                        logger.error(f"{type(self).__name__} 回复失败: error_type={type(e).__name__}:{e}")
                        return Reply(ReplyType.TEXT, ERROR_REPLY)
        finally:
            self._active_requests -= 1

    # ==================================================================
    # 4. 会话处理模板：历史加载/压缩 → 构建消息 → Agent Loop
    # ==================================================================

    async def _handle_session(self, query: str, context: Optional[Context], session_id: str) -> str:
        """会话级固定流程：历史管理 + 消息构建 + Agent Loop"""
        # 4.1 加载历史；超阈值时先用 LLM 压缩，再重新加载
        history = await self._load_and_compress_history(session_id)

        # 4.2 先持久化本轮用户消息，保证后续处理失败时对话记录也完整
        await self._db(self._session_manager.add_message, session_id=session_id, role="user", content=query)

        # 4.3 构建消息列表 + 执行 Agent Loop
        messages = await self._build_messages(query, history, context)
        answer = await self._run_agent_loop(messages, context, session_id=session_id)

        # 4.4 持久化最终回复（空回复不落库）
        if answer:
            await self._db(self._session_manager.add_message, session_id=session_id, role="assistant", content=answer)
        return answer

    async def _load_and_compress_history(self, session_id: str) -> List[Dict[str, Any]]:
        """加载会话历史，超阈值时压缩后重新加载"""
        logger.info(f"加载会话历史: session_id={session_id}")
        need_compress, history = await self._db(
            self._session_manager.should_compress, session_id,
        )
        if need_compress:
            logger.info(f"触发上下文压缩: session_id={session_id}")
            await self._session_manager.compress_history(
                session_id, self._summarize_text,
            )
            history = await self._db(
                self._session_manager.get_history, session_id,
            )
        return history

    # ==================================================================
    # 5. 消息构建（委托 MessageBuilder，子类可覆盖获取依赖和上下文）
    # ==================================================================

    async def _build_messages(
        self,
        query: str,
        history: List[Dict[str, Any]],
        context: Optional[Context] = None,
    ) -> List[Dict[str, Any]]:
        """构建 Agent Loop 的消息列表。

        流程：收集依赖 → 获取 RAG 上下文 → 预取数据 → 委托 MessageBuilder.build_messages。
        子类可覆盖 _get_tool_dependencies / _get_rag_context / _prefetch_data 提供数据。
        """
        logger.info(f"构建 Agent Loop 消息列表: query={query[:50]}")

        # 1. 收集工具依赖（shop_id、user_id 等可信身份参数）
        dependencies = self._get_tool_dependencies(context)

        # 2. 获取 RAG 上下文，注入 dependencies 供系统提示词占位符替换
        dependencies["rag_context"] = await self._get_rag_context(query, history, context)

        # 3. 预取商品目录，子类覆盖 _prefetch_data 实现
        dependencies["product_list"] = await self._prefetch_data(context)

        # 4. 委托 MessageBuilder 构建完整消息列表
        return self._message_builder.build_messages(query, history, dependencies)

    # ==================================================================
    # 6. ReAct Agent Loop 核心
    # ==================================================================

    async def _run_agent_loop(
        self,
        messages: List[Dict[str, Any]],
        context: Optional[Context],
        session_id: Optional[str] = None,
    ) -> str:
        """
        Agent ReAct 循环核心。

        流程：调用 LLM → 检查 tool_calls → 执行工具 → 回传结果 → 循环
        无 tool_calls 时返回 LLM 的文本回复。

        子类通过 _get_tools() 提供工具列表，
        通过 _get_tool_dependencies() 提供工具执行的上下文依赖。
        """
        # 绑定工具到 LLM（有工具时绑定，无工具时直接调用）
        tools = self._get_tools()
        llm = self._llm
        if tools:
            llm = self._llm.bind_tools(tools)

        loop_count = 0

        while loop_count < self._max_loops:
            # 1. 调用 LLM
            try:
                lc_messages = to_langchain_messages(messages)
                response: AIMessage = await llm.ainvoke(lc_messages)
            except Exception as e:
                logger.error(f"LLM 调用失败: error_type={type(e).__name__}:{e}")
                if loop_count == 0:
                    return "抱歉，AI 服务暂时不可用，请稍后再试。"
                # 已有中间结果，返回最近的 assistant 内容
                for msg in reversed(messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        return msg["content"]
                return "抱歉，AI 服务暂时不可用，请稍后再试。"

            # 2. 无工具调用 → 返回最终文本
            if not response.tool_calls:
                content = response.content or ""
                messages.append({"role": "assistant", "content": content})
                return content

            # 3. 保存 assistant 消息（包含 tool_calls）
            assistant_msg = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "type": "function",
                        "id": tc["id"],
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"], ensure_ascii=False)
                            if isinstance(tc["args"], dict) else str(tc["args"]),
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
            messages.append(assistant_msg)

            # 4. 检查循环上限 → 强制要求 LLM 给出最终回复
            # 注意：触顶收尾时不落库 assistant(tool_calls)、也不执行工具，
            # 否则会在历史里留下没有配对 tool 响应的孤立 tool_calls，
            # 导致下一轮 to_langchain_messages 触发 OpenAI 400、会话永久卡死。
            if loop_count >= self._max_loops - 1:
                logger.warning(f"工具调用达到上限 {self._max_loops}，强制结束循环")
                # 为本轮未执行的 tool_calls 补齐占位 tool 响应（仅内存、不落库），
                # 保证强制收尾那次 LLM 调用的消息序列合法（tool_calls 必须紧跟 tool 响应）。
                for tc in response.tool_calls:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "[已达到最大工具调用次数，该工具未执行]",
                    })
                messages.append({
                    "role": "user",
                    "content": "[已达到最大工具调用次数，请基于已有信息给出最终回复。]",
                })
                try:
                    lc_messages = to_langchain_messages(messages)
                    # 触顶后的强制收尾用未绑定工具的 self._llm：
                    # 此时模型无工具可调，必然输出纯文本，
                    # 避免它继续发起 tool_call 导致 content 为空、回复兜底失败。
                    final_response: AIMessage = await self._llm.ainvoke(lc_messages)
                    return final_response.content or assistant_msg["content"]
                except Exception:
                    return assistant_msg["content"]

            # 持久化 assistant 的工具调用记录
            # （移到触顶检查之后：只有确定要执行工具时才落库，避免产生孤立 tool_calls）
            if session_id and self._session_manager:
                await asyncio.to_thread(
                    self._session_manager.add_message,
                    session_id=session_id,
                    role="assistant",
                    content=json.dumps(
                        {"content": assistant_msg["content"], "tool_calls": assistant_msg["tool_calls"]},
                        ensure_ascii=False,
                    ),
                )

            # 5. 执行所有工具调用
            dependencies = self._get_tool_dependencies(context)
            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["args"]
                tool_call_id = tc["id"]

                result_content = await self._execute_tool(
                    tool_name, tool_args, dependencies,
                )

                # 追加 ToolMessage
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_content,
                })

                # 持久化工具执行结果
                if session_id and self._session_manager:
                    await asyncio.to_thread(
                        self._session_manager.add_message,
                        session_id=session_id,
                        role="tool",
                        content=result_content,
                        tool_call_id=tool_call_id,
                    )

            loop_count += 1

        # 兜底
        return messages[-1].get("content", "")

    # ==================================================================
    # 7. 工具执行（统一使用 @agent_tool 注册表）
    # ==================================================================

    async def _execute_tool(
        self,
        tool_name: str,
        tool_args: dict,
        dependencies: Dict[str, Any],
    ) -> str:
        """执行单个工具调用。

        默认实现：通过 @agent_tool 注册表的 execute_tool 执行。
        所有使用 @agent_tool 体系的 Agent 无需覆盖此方法。

        Args:
            tool_name: 工具名称
            tool_args: LLM 提供的参数字典
            dependencies: 可信依赖（shop_id、user_id 等）

        Returns:
            工具执行结果字符串
        """
        tool_start = time.monotonic()
        metrics.inc(f"tool.call.{tool_name}")
        try:
            from Agent.tool_decorator import execute_tool
            arguments_json = (
                json.dumps(tool_args, ensure_ascii=False)
                if isinstance(tool_args, dict) else str(tool_args)
            )
            result = await asyncio.to_thread(
                execute_tool, tool_name, arguments_json, dependencies,
            )
            elapsed_ms = (time.monotonic() - tool_start) * 1000
            metrics.observe("tool.latency_ms", elapsed_ms)
            return result
        except Exception as e:
            metrics.inc(f"tool.error.{tool_name}")
            logger.error(f"工具 {tool_name} 执行失败: error_type={type(e).__name__}:{e}")
            return "[工具执行失败，请稍后重试]"

    # ==================================================================
    # 8. 子类钩子：系统提示词 / 工具 / 上下文 / 依赖
    # ==================================================================

    def _get_system_prompt(self) -> str:
        """【子类可覆盖】返回系统提示词"""
        return ""

    def _get_tools(self) -> list:
        """【子类可覆盖】返回 LangChain BaseTool 列表，将绑定到 LLM"""
        return []

    async def _get_rag_context(
        self,
        query: str,
        history: List[Dict[str, Any]],
        context: Optional[Context] = None,
    ) -> str:
        """【子类可覆盖】获取额外上下文，拼入系统提示词。

        默认行为：自动执行 SelfRAG 精检索，将精选文档注入系统提示词。
        不需要 RAG 的子类（如 ChatAgent）覆盖返回空字符串即可。
        """
        shop_id = self._get_shop_id(context)
        if not shop_id:
            return ""

        shop_db_id = get_db_manager().resolve_shop_db_id(shop_id)
        if not shop_db_id:
            logger.warning(f"无法解析店铺数据库ID: shop_id={shop_id}")
            return ""

        try:
            from Agent.RAG.self_rag import get_shared_pipeline
            pipeline = get_shared_pipeline(shop_db_id)
            docs = await asyncio.to_thread(
                pipeline.retrieve_refined, shop_db_id, query, query,
            )
            if not docs:
                return ""
            context_max_length = get_config("agent.context_max_length", 3000)
            formatted = format_documents(docs)
            logger.info(f"SelfRAG 上下文检索: 检索到 {len(docs)} 篇文档")
            return f"以下是从知识库检索到的参考信息：\n{formatted[:context_max_length]}"
        except Exception as e:
            logger.warning(f"SelfRAG 上下文检索失败: error_type={type(e).__name__}")
            return ""

    def _get_tool_dependencies(self, context: Optional[Context]) -> Dict[str, Any]:
        """【子类可覆盖】返回工具执行时的可信依赖字典。

        默认实现：从 Context 提取 shop_id / user_id 等可信身份参数，
        防止 LLM 伪造身份参数。
        """
        from_uid = _context_value(context, "from_uid")

        # shop_id 保持整数类型，便于工具参数注入
        shop_id = _context_value(context, "shop_id") or 0
        if isinstance(shop_id, str) and shop_id.isdigit():
            shop_id = int(shop_id)

        return {
            "shop_name": _context_value(context, "shop_name"),
            "channel_type": str(context.channel_type.value if context and context.channel_type else ""),
            "shop_id": shop_id,
            "user_id": _context_value(context, "user_id"),
            "from_uid": from_uid,
            "recipient_uid": from_uid,
        }

    async def _prefetch_data(self, context: Optional[Context] = None) -> str:
        """【子类可覆盖】预取商品列表文本。

        默认实现：通过拼多多 HTTP 获取在售商品列表，
        不需要商品列表的子类（如 ChatAgent）覆盖返回空即可。
        """
        scope = context_scope(context) if context else {}
        shop_id = scope.get("shop_id")
        user_id = scope.get("user_id")
        if not shop_id or not user_id:
            return ""
        try:
            params = GetShopProductsParams(shop_id=shop_id, user_id=user_id)
            product_text = await asyncio.to_thread(get_shop_products, params)
            return product_text + PRODUCT_LIST_NOTE
        except Exception as e:
            logger.warning(f"商品列表预取失败: error_type={type(e).__name__}")
            return ""

    # ==================================================================
    # 9. 历史压缩
    # ==================================================================

    async def _summarize_text(self, messages: List[Dict[str, Any]]) -> str:
        """【子类可覆盖】用 LLM 生成对话摘要

        由 SessionManager.compress_history 直接调用，
        接收原始消息列表，子类覆盖时可拿到完整消息结构。
        """
        content = messages2content(messages)
        prompt = (
            "请简洁地总结以下对话的要点，保留关键信息和用户意图。\n\n"
            f"对话内容（共 {len(messages)} 条消息）：\n{content}"
        )
        try:
            response = await self._llm.ainvoke([
                {"role": "system", "content": "你是一个对话摘要助手。请简洁地总结对话要点。"},
                {"role": "user", "content": prompt},
            ])
            return response.content or "[摘要生成失败]"
        except Exception as e:
            logger.error(f"生成摘要失败: error_type={type(e).__name__}")
            return "[摘要生成失败]"

    # ==================================================================
    # 10. 通用工具方法
    # ==================================================================

    async def _db(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """把同步 DB 操作放到工作线程执行，避免阻塞事件循环"""
        return await asyncio.to_thread(func, *args, **kwargs)

    def _resolve_session_id(self, context: Optional[Context]) -> str:
        """解析会话 ID：有买家身份时按通道规则生成，否则用进程内兜底 ID"""
        if context is not None and context_scope(context).get("recipient_uid"):
            return make_conversation_key(context)
        return self._fallback_session_id

    @staticmethod
    def _get_shop_id(context: Optional[Context]) -> int:
        """从消息上下文中解析店铺原始 ID，取不到时返回 0"""
        if context is None:
            return 0
        raw = context_scope(context).get("shop_id") or 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
