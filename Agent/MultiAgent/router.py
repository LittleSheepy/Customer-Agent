"""
Agent/MultiAgent 多意图分类器
==============================

QueryClassifier：将用户问题分类到对应的子 Agent。
用于 Supervisor Agent 的意图识别与调度。
"""
from typing import Any, Optional
import asyncio
import time
from collections import OrderedDict

from langchain_core.prompts import ChatPromptTemplate

from config import get_config
from Agent.llm_client import create_chat_model
from Agent.MultiAgent.prompts import (
    INTENT_CLASSIFICATION_PROMPT,
    CHAT_FAST_PATH_PHRASES,
    AFTER_SALES_FAST_PATH_KEYWORDS,
    INTENT_SHIFT_KEYWORDS,
)
from Agent.MultiAgent.schemas import IntentClassification, IntentType
from utils.logger_loguru import get_logger
from utils.observability import metrics

logger = get_logger("MultiAgent.router")

# IntentType → 子 Agent 类名映射
_INTENT_TO_AGENT = {
    IntentType.PRODUCT_INQUIRY: "ProductAgent",
    IntentType.AFTER_SALES: "AfterSalesAgent",
    IntentType.CHAT: "ChatAgent",
}


class QueryClassifier:
    """多意图分类器：将用户问题分类到子 Agent

    用于 Supervisor Agent 的意图识别与调度。
    分类失败时默认回退到商品咨询子 Agent。
    """

    def __init__(self):
        # include_raw=True 避免 Pydantic 序列化警告，手动提取 parsed 字段
        self._classifier = create_chat_model(temperature=get_config("llm.eval_temperature", 0.1)).with_structured_output(
            IntentClassification, include_raw=True,
        )
        # 会话级意图缓存：session_id -> (intent, confidence, monotonic_ts)，LRU 有界
        self._intent_cache: "OrderedDict[str, tuple]" = OrderedDict()

    # ------------------------------------------------------------------
    # 降本快速通道：归一化 / 规则判定 / 会话缓存 / 漂移熔断
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(text: str) -> str:
        """归一化：去首尾空白、去结尾常见标点、转小写"""
        t = (text or "").strip().lower()
        while t and t[-1] in "。.!！?？~～,，、 ":
            t = t[:-1]
        return t.strip()

    def _fast_classify(self, question: str) -> Optional[IntentClassification]:
        """规则快速通道：明确场景直接判定，跳过 LLM。

        - 强转人工/售后信号：子串匹配 → after_sales
        - 闲聊白名单：归一化后全等匹配 → chat
        命中返回 IntentClassification，否则 None（交给 LLM）。
        """
        if not get_config("agent.classify_fast_path_enabled", True):
            return None
        normalized = self._normalize(question)
        if not normalized:
            return None
        # 强售后信号优先（子串匹配，语义无歧义）
        for kw in AFTER_SALES_FAST_PATH_KEYWORDS:
            if kw in normalized:
                return IntentClassification(
                    intent=IntentType.AFTER_SALES, confidence=0.9,
                    reasoning=f"快速通道命中售后关键词: {kw}",
                )
        # 闲聊白名单（严格全等，避免“你好，这个多少钱”被误判）
        if normalized in CHAT_FAST_PATH_PHRASES:
            return IntentClassification(
                intent=IntentType.CHAT, confidence=0.95,
                reasoning="快速通道命中闲聊白名单",
            )
        return None

    def _detect_intent_shift(self, question: str, cached_intent: IntentType) -> bool:
        """意图漂移检测：新消息是否命中“非缓存意图”的强关键词"""
        normalized = self._normalize(question)
        for intent_value, keywords in INTENT_SHIFT_KEYWORDS.items():
            if intent_value == cached_intent.value:
                continue
            if any(kw in normalized for kw in keywords):
                return True
        return False

    def _cache_get(self, session_id: Optional[str], question: str) -> Optional[IntentClassification]:
        """读取会话级意图缓存（含过期/置信度/漂移熔断判定）"""
        if not session_id or not get_config("agent.classify_cache_enabled", True):
            return None
        ttl = get_config("agent.classify_cache_ttl", 60.0)
        min_conf = get_config("agent.classify_cache_min_confidence", 0.85)
        entry = self._intent_cache.get(session_id)
        if not entry:
            return None
        intent, confidence, ts = entry
        # 过期
        if time.monotonic() - ts > ttl:
            self._intent_cache.pop(session_id, None)
            return None
        # 置信度不足（不删缓存，仅不复用）
        if confidence < min_conf:
            return None
        # 意图漂移熔断：命中其他意图强关键词则作废缓存
        if self._detect_intent_shift(question, intent):
            self._intent_cache.pop(session_id, None)
            logger.info(
                f"意图漂移，放弃会话缓存: session={session_id[:16]}... cached={intent.value}"
            )
            return None
        # 命中：LRU 刷新 + 滑动时间窗
        self._intent_cache[session_id] = (intent, confidence, time.monotonic())
        self._intent_cache.move_to_end(session_id)
        return IntentClassification(
            intent=intent, confidence=confidence,
            reasoning="会话级意图缓存命中",
        )

    def _cache_set(self, session_id: Optional[str], classification: IntentClassification) -> None:
        """写入会话级意图缓存（仅高置信度结果，LRU 有界）"""
        if not session_id or not get_config("agent.classify_cache_enabled", True):
            return
        min_conf = get_config("agent.classify_cache_min_confidence", 0.85)
        if classification.confidence < min_conf:
            return
        max_sessions = get_config("agent.classify_cache_max_sessions", 1000)
        self._intent_cache[session_id] = (
            classification.intent, classification.confidence, time.monotonic(),
        )
        self._intent_cache.move_to_end(session_id)
        while len(self._intent_cache) > max_sessions:
            evicted, _ = self._intent_cache.popitem(last=False)
            logger.debug(f"LRU 淘汰意图缓存: session={evicted[:16]}...")

    # ------------------------------------------------------------------
    # 分类入口
    # ------------------------------------------------------------------

    async def classify(self, question: str, session_id: Optional[str] = None) -> IntentClassification:
        """分类用户意图：规则快速通道 → 会话缓存 → LLM，失败或超时则回退商品咨询"""
        # 1. 规则快速通道（最便宜，永远先试）
        fast = self._fast_classify(question)
        if fast is not None:
            metrics.inc("intent.source.fastpath")
            logger.info(f"意图分类[快速通道]: {fast.intent.value} (confidence={fast.confidence:.2f})")
            return fast

        # 2. 会话级意图缓存
        cached = self._cache_get(session_id, question)
        if cached is not None:
            metrics.inc("intent.source.cache")
            logger.info(f"意图分类[缓存命中]: {cached.intent.value} (confidence={cached.confidence:.2f})")
            return cached

        # 3. LLM 分类
        prompt = ChatPromptTemplate.from_template(INTENT_CLASSIFICATION_PROMPT)
        timeout = get_config("agent.classify_timeout", 30.0)
        try:
            raw_result = await asyncio.wait_for(
                self._classifier.ainvoke(prompt.format(question=question)),
                timeout=timeout,
            )
            result: IntentClassification = raw_result["parsed"]
            if result is None:
                raise ValueError("parsed 结果为 None")
            logger.info(f"意图分类[LLM]: {result.intent.value} (confidence={result.confidence:.2f})")
            metrics.inc("intent.source.llm")
            # 写入会话缓存（仅高置信度）
            self._cache_set(session_id, result)
            return result
        except Exception as e:
            logger.warning(f"意图分类失败，回退商品咨询: error_type={type(e).__name__}:{e}")
            return IntentClassification(
                intent=IntentType.PRODUCT_INQUIRY,
                confidence=0.5,
                reasoning="分类失败，使用默认商品咨询",
            )

    async def decide_agent(
        self, question: str, session_id: Optional[str] = None,
    ) -> tuple[str, IntentClassification]:
        """分类并返回目标子 Agent 名称

        Returns:
            (agent_name, classification): 子 Agent 名称和分类结果
        """
        classification = await self.classify(question, session_id)
        agent_name = _INTENT_TO_AGENT.get(classification.intent, "ProductAgent")
        return agent_name, classification
