"""
Agent 韧性层 — 熔断器 + 指数退避重试
======================================

设计思路
--------
LLM API 调用是客服系统最脆弱的环节：网络抖动、限流、服务端 5xx 都可能导致
单次调用失败。如果不做保护，一条失败的 LLM 调用直接让用户收到"稍后再试"。

本模块提供两层防护：

1. **重试（Retry）**：瞬时故障（网络超时、偶发 5xx）通过指数退避重试恢复。
2. **熔断（Circuit Breaker）**：连续失败说明下游不可用，快速失败而非让所有
   请求排队等待超时，保护系统不被雪崩式请求压垮。

状态机：
    CLOSED（正常）──连续失败 N 次──▶ OPEN（熔断）
        ▲                               │
        │                          等待冷却期
        │                               ▼
        └──────半开探测成功──────── HALF_OPEN
                  │
                  探测失败 ──▶ OPEN（重新熔断）

用法：
    model = create_chat_model(temperature=0.3)
    # model 已经是 ResilientModel，直接调用即可
    response = await model.ainvoke(messages)
"""
from __future__ import annotations

import asyncio
import enum
import time
from typing import Any, Optional

from utils.logger_loguru import get_logger

logger = get_logger("Agent.resilience")


# ======================================================================
# 熔断器状态
# ======================================================================

class CircuitState(enum.Enum):
    """熔断器三态"""
    CLOSED = "closed"        # 正常放行
    OPEN = "open"            # 熔断中，快速失败
    HALF_OPEN = "half_open"  # 冷却期后，放行探测请求


class CircuitOpenError(Exception):
    """熔断器开启时抛出的异常"""


# ======================================================================
# 熔断器核心
# ======================================================================

class CircuitBreaker:
    """三态熔断器

    参数：
        failure_threshold: 连续失败多少次后进入 OPEN
        recovery_timeout: OPEN 后多少秒自动转入 HALF_OPEN

    线程安全：由 asyncio 单线程事件循环保证，无需额外加锁。
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0

    @property
    def state(self) -> CircuitState:
        """当前状态（OPEN 到期自动转为 HALF_OPEN）"""
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("熔断器: OPEN → HALF_OPEN（冷却期结束，允许探测）")
        return self._state

    @property
    def is_available(self) -> bool:
        """是否可以放行请求"""
        return self.state != CircuitState.OPEN

    def record_success(self) -> None:
        """记录成功"""
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            logger.info("熔断器: HALF_OPEN → CLOSED（探测成功，恢复正常）")
        # CLOSED 状态下成功，重置连续失败计数
        self._failure_count = 0

    def record_failure(self) -> None:
        """记录失败"""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            # 探测失败，重新熔断
            self._state = CircuitState.OPEN
            logger.warning("熔断器: HALF_OPEN → OPEN（探测失败，重新熔断）")
        elif self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                f"熔断器: CLOSED → OPEN（连续失败 {self._failure_count} 次，"
                f"熔断 {self._recovery_timeout}s）"
            )

    def get_stats(self) -> dict:
        """获取熔断器统计信息"""
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
        }


# ======================================================================
# 韧性 LLM 包装器
# ======================================================================

class ResilientModel:
    """带重试 + 熔断的 LLM 调用包装器

    对 LangChain ChatModel 的 ainvoke 做透明增强：
    - 熔断器：连续失败达阈值后快速失败，不再排队等超时
    - 指数退避重试：0.5s → 1s（默认最多重试 2 次）
    """

    def __init__(
        self,
        model: Any,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        _shared_circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        """
        Args:
            model: LangChain ChatModel 实例（需支持 ainvoke / bind_tools）
            max_retries: 最大重试次数（不含首次调用）
            backoff_base: 退避基数（秒），实际等待 = base * 2^attempt
            failure_threshold: 连续失败多少次触发熔断
            recovery_timeout: 熔断冷却时间（秒）
            _shared_circuit_breaker: 内部参数，bind_tools 时共享同一个熔断器
        """
        self._model = model
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._circuit_breaker = _shared_circuit_breaker or CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )
        # 统计
        self._total_calls = 0
        self._total_retries = 0

    def bind_tools(self, tools: list) -> "ResilientModel":
        """透传 bind_tools 到底层模型，返回新的 ResilientModel 包装

        新实例共享同一个熔断器，确保熔断状态全局一致。
        """
        bound_model = self._model.bind_tools(tools)
        return ResilientModel(
            model=bound_model,
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            _shared_circuit_breaker=self._circuit_breaker,
        )

    def with_structured_output(self, schema: Any, **kwargs) -> Any:
        """透传 with_structured_output 到底层模型，返回受韧性保护的代理

        返回的 _ProtectedRunnable 拦截 ainvoke/invoke，
        使其经过熔断器 + 重试保护，而非直接调用底层 ChatOpenAI。
        """
        runnable = self._model.with_structured_output(schema, **kwargs)
        return _ProtectedRunnable(runnable, self)

    # ------------------------------------------------------------------
    # 核心保护逻辑（供 ainvoke / invoke / _ProtectedRunnable 共享）
    # ------------------------------------------------------------------

    async def _protected_ainvoke(self, call_fn):
        """带熔断 + 重试的异步调用核心"""
        self._total_calls += 1
        if not self._circuit_breaker.is_available:
            raise CircuitOpenError(
                f"LLM 服务熔断中（{self._circuit_breaker.get_stats()}），快速失败"
            )
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                result = await call_fn()
                self._circuit_breaker.record_success()
                return result
            except Exception as e:
                last_exc = e
                self._circuit_breaker.record_failure()
                if attempt < self._max_retries:
                    wait = self._backoff_base * (2 ** attempt)
                    self._total_retries += 1
                    logger.warning(
                        f"LLM 调用失败 (attempt {attempt + 1}/{self._max_retries + 1}), "
                        f"{wait:.1f}s 后重试: {type(e).__name__}"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        f"LLM 调用重试耗尽 ({self._max_retries + 1} 次): "
                        f"{type(e).__name__}: {e}"
                    )
        raise last_exc

    def _protected_invoke(self, call_fn):
        """带熔断 + 重试的同步调用核心"""
        self._total_calls += 1
        if not self._circuit_breaker.is_available:
            raise CircuitOpenError(
                f"LLM 服务熔断中（{self._circuit_breaker.get_stats()}），快速失败"
            )
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                result = call_fn()
                self._circuit_breaker.record_success()
                return result
            except Exception as e:
                last_exc = e
                self._circuit_breaker.record_failure()
                if attempt < self._max_retries:
                    wait = self._backoff_base * (2 ** attempt)
                    self._total_retries += 1
                    logger.warning(
                        f"LLM 同步调用失败 (attempt {attempt + 1}/{self._max_retries + 1}), "
                        f"{wait:.1f}s 后重试: {type(e).__name__}"
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"LLM 同步调用重试耗尽 ({self._max_retries + 1} 次): "
                        f"{type(e).__name__}: {e}"
                    )
        raise last_exc

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def invoke(self, messages: list, **kwargs) -> Any:
        """带韧性保护的 LLM 同步调用（供同步管线如 SelfRAG 使用）"""
        return self._protected_invoke(lambda: self._model.invoke(messages, **kwargs))

    async def ainvoke(self, messages: list, **kwargs) -> Any:
        """带韧性保护的 LLM 异步调用"""
        return await self._protected_ainvoke(lambda: self._model.ainvoke(messages, **kwargs))

    def get_stats(self) -> dict:
        """获取韧性层统计信息"""
        return {
            "total_calls": self._total_calls,
            "total_retries": self._total_retries,
            "circuit_breaker": self._circuit_breaker.get_stats(),
        }


# ======================================================================
# 结构化输出代理
# ======================================================================

class _ProtectedRunnable:
    """代理 Runnable，使 ainvoke/invoke 经过 ResilientModel 的韧性保护

    LangChain 的 with_structured_output 返回的 Runnable 内部直接引用 ChatOpenAI，
    调用时绕过 ResilientModel。此类拦截调用，委托给 ResilientModel 的受保护路径。
    """

    def __init__(self, runnable: Any, resilient_model: ResilientModel):
        self._runnable = runnable
        self._resilient_model = resilient_model

    async def ainvoke(self, *args, **kwargs):
        """经过熔断器 + 重试保护的异步调用"""
        return await self._resilient_model._protected_ainvoke(
            lambda: self._runnable.ainvoke(*args, **kwargs)
        )

    def invoke(self, *args, **kwargs):
        """经过熔断器 + 重试保护的同步调用"""
        return self._resilient_model._protected_invoke(
            lambda: self._runnable.invoke(*args, **kwargs)
        )
