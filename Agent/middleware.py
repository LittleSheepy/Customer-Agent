"""
Agent 中间件管道 — 洋葱模型 AOP
================================

设计思路
--------
将横切关注点（日志、指标、超时、校验）从核心业务逻辑中抽离，
以"洋葱模型"组合：每层中间件包裹核心处理，请求进入时逐层穿透，
回复返回时逐层穿出，每层可在前后两个阶段做事。

中间件签名：
    async def middleware(query, context, next_fn) -> Reply:
        # 前置（请求进入）
        reply = await next_fn(query, context)  # 交给下一层
        # 后置（回复返回）
        return reply

Pipeline 通过递归 next_fn 构建调用链，无需手动嵌套。

用法：
    pipeline = MiddlewarePipeline(handler=core_reply)
    pipeline.use(logging_middleware).use(metrics_middleware)
    reply = await pipeline.execute(query, context)
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, List, Optional

from bridge.context import Context
from bridge.reply import Reply, ReplyType
from utils.logger_loguru import get_logger
from utils.observability import get_request_id, metrics

logger = get_logger("Agent.middleware")

# 中间件类型别名
Middleware = Callable[
    [str, Optional[Context], Callable[..., Awaitable[Reply]]],
    Awaitable[Reply],
]


# ======================================================================
# 管道核心
# ======================================================================

class MiddlewarePipeline:
    """中间件管道：按注册顺序构建洋葱模型调用链

    执行顺序：
        请求 → middleware[0]前置 → middleware[1]前置 → ... → handler
        回复 ← middleware[0]后置 ← middleware[1]后置 ← ... ← handler
    """

    def __init__(self, handler: Callable[[str, Optional[Context]], Awaitable[Reply]]):
        """
        Args:
            handler: 核心处理函数（洋葱的"核"），签名 (query, context) -> Reply
        """
        self._handler = handler
        self._middlewares: List[Middleware] = []

    def use(self, middleware: Middleware) -> "MiddlewarePipeline":
        """注册一个中间件，支持链式调用"""
        self._middlewares.append(middleware)
        return self

    async def execute(self, query: str, context: Optional[Context] = None) -> Reply:
        """执行管道：依次穿过所有中间件，最终到达核心处理"""
        index = 0

        async def next_fn(q: str, ctx: Optional[Context]) -> Reply:
            nonlocal index
            if index < len(self._middlewares):
                mw = self._middlewares[index]
                index += 1
                return await mw(q, ctx, next_fn)
            # 所有中间件穿透后，到达核心处理
            return await self._handler(q, ctx)

        return await next_fn(query, context)


# ======================================================================
# 内置中间件
# ======================================================================

async def logging_middleware(
    query: str,
    context: Optional[Context],
    next_fn: Callable[..., Awaitable[Reply]],
) -> Reply:
    """日志中间件：记录请求开始/结束 + request_id + 耗时"""
    import time

    request_id = get_request_id()
    query_preview = query[:50] + ("..." if len(query) > 50 else "")
    logger.info(f"[{request_id}] 请求开始: {query_preview}")

    start = time.monotonic()
    reply = await next_fn(query, context)
    elapsed_ms = (time.monotonic() - start) * 1000

    logger.info(
        f"[{request_id}] 请求结束: type={reply.type.name}, "
        f"耗时={elapsed_ms:.0f}ms"
    )
    return reply


async def metrics_middleware(
    query: str,
    context: Optional[Context],
    next_fn: Callable[..., Awaitable[Reply]],
) -> Reply:
    """指标中间件：记录回复耗时到直方图 + 请求计数"""
    import time

    metrics.inc("request.count")

    start = time.monotonic()
    reply = await next_fn(query, context)
    elapsed_ms = (time.monotonic() - start) * 1000

    metrics.observe("reply.latency_ms", elapsed_ms)
    return reply


def timeout_middleware(timeout_seconds: float = 180.0) -> Middleware:
    """超时中间件工厂：超过指定时间自动返回超时回复

    用法：
        pipeline.use(timeout_middleware(timeout_seconds=30.0))
    """
    async def _middleware(
        query: str,
        context: Optional[Context],
        next_fn: Callable[..., Awaitable[Reply]],
    ) -> Reply:
        try:
            return await asyncio.wait_for(
                next_fn(query, context),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[{get_request_id()}] 请求超时 ({timeout_seconds}s)"
            )
            return Reply(ReplyType.TEXT, "处理超时，请稍后再试。")

    return _middleware


async def input_validation_middleware(
    query: str,
    context: Optional[Context],
    next_fn: Callable[..., Awaitable[Reply]],
) -> Reply:
    """输入校验中间件：拦截空查询和过长查询"""
    max_length = 5000

    if not query or not query.strip():
        logger.warning(f"[{get_request_id()}] 空查询被拦截")
        return Reply(ReplyType.TEXT, "请输入您的问题。")

    if len(query) > max_length:
        logger.warning(
            f"[{get_request_id()}] 查询过长 ({len(query)} 字符)，截断至 {max_length}"
        )
        query = query[:max_length]

    return await next_fn(query, context)
