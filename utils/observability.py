"""
可观测性 — 请求关联 ID + 轻量指标采集
======================================

设计思路
--------
1. **RequestContext（请求关联 ID）**
   每条用户消息进来时生成唯一 request_id，存入 ContextVar。
   该请求经过的所有协程都能读到同一个 request_id，用于串联日志。
   ContextVar 天然按 asyncio 任务隔离，并发请求互不干扰。

2. **Metrics（计数器 + 直方图 + 百分位）**
   不依赖 Prometheus 等外部库，纯内存实现：
   - 计数器（Counter）：只增不减，统计调用次数
   - 直方图（Histogram）：按预定义桶边界统计数值分布，近似百分位
   内存开销 = O(指标数 × 桶数量)，恒定可控。

用法：
    from utils.observability import request_scope, get_request_id, metrics

    async def handle_request(query):
        async with request_scope(agent="supervisor"):
            metrics.inc("request.count")
            # ... 业务逻辑 ...
            metrics.observe("reply.latency_ms", elapsed_ms)
"""
from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from utils.logger_loguru import get_logger

logger = get_logger("observability")


# ======================================================================
# 1. 请求上下文（关联 ID）
# ======================================================================

@dataclass
class RequestContext:
    """单次请求的上下文信息

    Attributes:
        request_id: 唯一标识，12 位 hex，足够短但碰撞概率极低
        start_time: monotonic 时间戳，用于计算耗时
        agent_name: 处理该请求的 Agent 名称
        intent: 意图分类结果（可选，后续填充）
        session_id: 会话 ID（可选，后续填充）
    """
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    start_time: float = field(default_factory=time.monotonic)
    agent_name: str = ""
    intent: str = ""
    session_id: str = ""


# ContextVar：每个 asyncio 任务自动隔离
_request_ctx: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar(
    "_request_ctx", default=None,
)


@asynccontextmanager
async def request_scope(
    agent: str = "",
    session_id: str = "",
):
    """请求级上下文管理器

    进入时创建 RequestContext，退出时自动清理。
    并发请求各自持有独立 context，互不干扰。

    用法：
        async with request_scope(agent="supervisor", session_id="xxx") as ctx:
            # ctx.request_id 可打印到日志
            ...
    """
    ctx = RequestContext(agent_name=agent, session_id=session_id)
    token = _request_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _request_ctx.reset(token)


def get_request_id() -> str:
    """获取当前请求的 request_id，无上下文时返回 -"""
    ctx = _request_ctx.get()
    return ctx.request_id if ctx else "-"


def get_request_context() -> Optional[RequestContext]:
    """获取当前请求的完整上下文，无上下文时返回 None"""
    return _request_ctx.get()


# ======================================================================
# 2. 轻量指标采集（计数器 + 直方图）
# ======================================================================

# 默认直方图桶边界（毫秒 / 置信度等通用场景）
_DEFAULT_HISTOGRAM_BUCKETS = [
    10, 25, 50, 100, 200, 500, 1000, 2000, 5000, 10000,
]


class Metrics:
    """轻量指标采集器

    - counter：dict[str, int]，原子 inc()
    - histogram：dict[str, list[int]]，每个指标按桶边界统计落入次数

    线程安全：由 asyncio 单线程事件循环保证，无需额外加锁。
    """

    def __init__(self, buckets: Optional[List[float]] = None):
        self._counters: Dict[str, int] = {}
        self._histograms: Dict[str, Dict[str, int]] = {}
        self._buckets = sorted(buckets or _DEFAULT_HISTOGRAM_BUCKETS)

    # ---------- 计数器 ----------

    def inc(self, name: str, value: int = 1) -> None:
        """计数器 +value（默认 +1）"""
        self._counters[name] = self._counters.get(name, 0) + value

    def get_counter(self, name: str) -> int:
        """获取计数器当前值"""
        return self._counters.get(name, 0)

    # ---------- 直方图 ----------

    def observe(self, name: str, value: float) -> None:
        """将一次观测值记入对应桶

        桶结构：{"bucket_<boundary>": count, "count": total, "sum": total_sum}
        """
        if name not in self._histograms:
            self._histograms[name] = {
                f"bucket_{b}": 0 for b in self._buckets
            }
            self._histograms[name]["count"] = 0
            self._histograms[name]["sum"] = 0.0

        h = self._histograms[name]
        h["count"] += 1
        h["sum"] += value

        # 累加式桶计数：value <= bucket_boundary 则该桶 +1
        for b in self._buckets:
            if value <= b:
                h[f"bucket_{b}"] += 1

    def percentile(self, name: str, p: float) -> float:
        """近似百分位值（P50 / P95 / P99）

        原理：找到第一个累计占比 >= p 的桶边界。
        无数据时返回 0.0。
        """
        h = self._histograms.get(name)
        if not h or h["count"] == 0:
            return 0.0

        total = h["count"]
        target = total * p / 100.0
        cumulative = 0

        for b in self._buckets:
            cumulative += h[f"bucket_{b}"]
            if cumulative >= target:
                return b

        # 所有桶都没覆盖到（超过最大桶边界），返回最大桶边界
        return self._buckets[-1]

    # ---------- 快照 ----------

    def snapshot(self) -> dict:
        """导出所有指标的快照（供日志 / 健康检查使用）"""
        result = {
            "counters": dict(self._counters),
            "histograms": {},
        }
        for name, h in self._histograms.items():
            result["histograms"][name] = {
                "count": h["count"],
                "sum": round(h["sum"], 2),
                "p50": self.percentile(name, 50),
                "p95": self.percentile(name, 95),
                "p99": self.percentile(name, 99),
            }
        return result

    def reset(self) -> None:
        """清空所有指标（测试用）"""
        self._counters.clear()
        self._histograms.clear()


# 全局单例
metrics = Metrics()
