"""
Agent/AgenticRAGAgent 自反思 RAG 闭环
=====================================

参照 UltimateRAG stage_3/self_rag.py 的核心闭环：

    检索知识库 -> 文档相关性评估过滤 -> 生成答案
    -> 答案质量评估 -> 查询优化 -> 迭代检索（最多 self_rag.max_iterations 轮）

知识检索复用 database.knowledge_service.KnowledgeService（需 shop_id）。
"""
from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Union

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

from Agent.RAG.Reranker.reranker import Reranker
from Agent.RAG.Retriever.hybrid_retriever import HybridRetriever
from Agent.llm_client import create_chat_model
from Agent.AgenticRAGAgent.prompts import (
    ANSWER_PROMPT,
    QUALITY_PROMPT,
    QUERY_REFINEMENT_PROMPT,
    RELEVANCE_PROMPT,
)
from Agent.AgenticRAGAgent.schemas import (
    QualityEvaluation,
    QualityGrade,
    RelevanceEvaluation,
    RelevanceGrade,
)
from Agent.RAG.vector_store import VectorSearchService
from Agent.utils import to_documents, format_documents
from database.knowledge_service import KnowledgeService
from config import get_config
from utils.logger_loguru import get_logger
from core.di_container import container

logger = get_logger("AgenticRAGAgent.self_rag")

# 按店铺缓存的 SelfRAGPipeline 实例（LRU 淘汰，上限 20）
_MAX_PIPELINES = 20
_pipelines_by_shop: OrderedDict[int, "SelfRAGPipeline"] = OrderedDict()
_pipeline_lock = threading.Lock()


def get_shared_pipeline(shop_db_id: int) -> "SelfRAGPipeline":
    """获取或创建指定店铺的 SelfRAGPipeline 实例

    每个店铺的知识文档、BM25 索引、向量集合相互隔离，
    因此 SelfRAGPipeline（及其内部的 HybridRetriever）必须按 shop_db_id 分派。
    使用 LRU 策略淘汰最久未使用的实例，防止内存泄漏。

    Args:
        shop_db_id: 店铺数据库 ID（Shop.id）

    Returns:
        该店铺专属的 SelfRAGPipeline 实例
    """
    # 无锁 fast path：.get() 为单次原子读取，拿到强引用后即使随后被并发淘汰
    # 也不会 KeyError（对象已被局部引用持有）；避免了 in→[] 两步非原子的竞态
    pipeline = _pipelines_by_shop.get(shop_db_id)
    if pipeline is not None:
        # 尽力更新 LRU recency；若此刻正被并发淘汰导致 KeyError 则忽略
        # （已持有强引用，返回不受影响；move_to_end 为 GIL 原子操作，不会损坏结构）
        try:
            _pipelines_by_shop.move_to_end(shop_db_id)
        except KeyError:
            pass
        return pipeline

    with _pipeline_lock:
        # 双重检查：等锁期间可能已被其他线程创建
        pipeline = _pipelines_by_shop.get(shop_db_id)
        if pipeline is not None:
            _pipelines_by_shop.move_to_end(shop_db_id)
            return pipeline

        account_info = {"id": shop_db_id}
        pipeline = SelfRAGPipeline(account_info=account_info)
        _pipelines_by_shop[shop_db_id] = pipeline
        logger.info(f"创建店铺 {shop_db_id} 的 SelfRAGPipeline")

        # LRU 淘汰：超出上限时移除最久未使用的
        while len(_pipelines_by_shop) > _MAX_PIPELINES:
            evicted_id, _ = _pipelines_by_shop.popitem(last=False)
            logger.info(f"LRU 淘汰 SelfRAGPipeline: shop_db_id={evicted_id}")

        # 返回锁内持有的局部引用：即使刚创建的条目立即被并发淘汰也不会 KeyError
        return pipeline



class SelfRAGPipeline:
    """自反思 RAG 管线：检索-评估-生成-迭代"""

    def __init__(self, account_info=None):
        """
        Args:
            llm: 生成用 LLM
            relevance_evaluator: 绑定 RelevanceEvaluation 的结构化输出链
            quality_evaluator: 绑定 QualityEvaluation 的结构化输出链
            vector_store: 向量检索服务（VectorSearchService），None 时降级纯关键词
        """
        self.account_info = account_info
        self._llm = create_chat_model(temperature=get_config("llm.generate_temperature", 0.3))
        eval_llm = create_chat_model(temperature=get_config("llm.eval_temperature", 0.1))
        self._relevance_evaluator = eval_llm.with_structured_output(RelevanceEvaluation, include_raw=True)
        self._quality_evaluator = eval_llm.with_structured_output(QualityEvaluation, include_raw=True)
        self._knowledge_service = container.get(KnowledgeService)
        self._vector_store : Optional[VectorSearchService] = container.get(VectorSearchService)
        self._hybrid_retriever : Optional[HybridRetriever] = container.get(HybridRetriever, account_info=self.account_info)
        self._rerank : Reranker = Reranker()
    # ------------------------------------------------------------------
    # 对外主入口
    # ------------------------------------------------------------------

    async def run(self, question: str, retrieval_query: str, history_text: str, shop_id: int) -> str:
        """检索 -> 相关性过滤 -> 生成 -> 质量评估 -> 迭代优化

        异步入口，内部通过 asyncio.to_thread 调用同步方法，
        避免阻塞事件循环（底层 I/O 服务本身是同步的）。
        """
        # 1. 首次检索
        docs = await asyncio.to_thread(self._retrieve, shop_id, retrieval_query)
        if not docs:
            logger.info("知识库未检索到任何文档，转人工兜底")
            return get_config("self_rag.human_agent_hint", "抱歉，这个问题我暂时没能为您找到准确答案，建议您联系人工客服处理哦～")
        docs = self._rerank.rerank(question, docs)
        # 2. 相关性评估过滤
        filtered = await asyncio.to_thread(self._filter_relevant_documents, question, docs)
        if not filtered:
            logger.info("全部文档相关性不足，转人工兜底")
            return get_config("self_rag.human_agent_hint", "抱歉，这个问题我暂时没能为您找到准确答案，建议您联系人工客服处理哦～")

        seen_contents = {doc.page_content for doc in filtered}

        # 3. 生成首版答案
        context_text = format_documents(filtered)
        answer = await asyncio.to_thread(self._generate_answer, question, history_text, context_text)

        # 4. 质量评估，不达标则迭代检索
        quality = await asyncio.to_thread(self._evaluate_answer_quality, question, context_text, answer)
        iterations = 1
        while self._should_iterate(quality) and iterations < get_config("self_rag.max_iterations", 3):
            iterations += 1
            logger.info(f"开始第 {iterations} 轮自反思迭代")

            # 优化查询并重新检索
            refined_query = await asyncio.to_thread(
                self._refine_query, question, context_text[:500], quality.missing_info,
            )
            new_docs = await asyncio.to_thread(self._retrieve, shop_id, refined_query)

            # 合并去重后重新生成
            for doc in new_docs:
                if doc.page_content not in seen_contents:
                    seen_contents.add(doc.page_content)
                    filtered.append(doc)

            context_text = format_documents(filtered)
            answer = await asyncio.to_thread(self._generate_answer, question, history_text, context_text)
            quality = await asyncio.to_thread(self._evaluate_answer_quality, question, context_text, answer)
            logger.info(f"第 {iterations} 轮质量评估: {quality.grade.value} (分数: {quality.score:.2f})")

        # 5. 多轮迭代后仍然不足，转人工兜底
        if quality.grade == QualityGrade.INSUFFICIENT or quality.score < 0.5:
            logger.info(f"答案质量仍不足: grade={quality.grade.value} score={quality.score:.2f}，转人工兜底")
            return get_config("self_rag.human_agent_hint", "抱歉，这个问题我暂时没能为您找到准确答案，建议您联系人工客服处理哦～")

        return answer

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def _retrieve(self, shop_db_id: int, query: str) -> List[Document]:
        """混合检索：向量相似度 + 关键词，合并去重"""
        result = self._knowledge_service.search_knowledge(
            shop_db_id=shop_db_id, query=query, limit=get_config("retriever.retrieval_limit", 10),
        )
        keyword_docs = to_documents(
            result.get("product_knowledge", []),
            result.get("customer_service_knowledge", []),
        )
        vector_docs = self._hybrid_search(query)
        merged: List[Document] = []
        seen: set = set()
        for doc in vector_docs + keyword_docs:
            if doc.page_content not in seen:
                seen.add(doc.page_content)
                merged.append(doc)
        if vector_docs:
            logger.info(f"混合检索: 向量 {len(vector_docs)} + 关键词 {len(keyword_docs)} -> 去重后 {len(merged)}")
        return merged

    def _vector_search(self, shop_id: int, query: str) -> List[Document]:
        """向量相似度检索（使用 VectorSearchService），未启用或失败时返回空列表"""
        if self._vector_store is None:
            return []
        try:
            return self._vector_store.search(shop_id, query, get_config("embedding.top_k", 5))
        except Exception as e:
            logger.warning(
                f"向量检索失败，降级关键词: error_type={type(e).__name__}, "
                f"detail={str(e)[:200]}"
            )
            return []

    def _hybrid_search(self, query: str) -> List[Document]:
        """向量相似度检索（使用 HybridRetriever），未启用或失败时返回空列表（降级纯关键词）"""
        if self._hybrid_retriever is None:
            return []
        try:
            return self._hybrid_retriever.search(query, get_config("embedding.top_k", 5))
        except Exception as e:
            logger.warning(f"向量检索失败: {type(e).__name__}")
            return []

    @staticmethod
    def _to_documents(result: Dict[str, Any]) -> List[Document]:
        """把 KnowledgeService 的检索结果转为 LangChain Document"""
        return to_documents(
            result.get("product_knowledge", []),
            result.get("customer_service_knowledge", []),
        )

    # ------------------------------------------------------------------
    # 相关性评估
    # ------------------------------------------------------------------

    def _filter_relevant_documents(
        self, question: str, documents: List[Document],
    ) -> List[Document]:
        """评估每个文档的相关性，保留达标的文档"""
        filtered = []
        for doc in documents:
            evaluation = self._evaluate_relevance(question, doc)
            if evaluation.score >= get_config("self_rag.relevance_threshold", 0.5):
                filtered.append(doc)
        logger.info(f"相关性过滤: {len(documents)} -> {len(filtered)} 个文档")
        return filtered

    def _evaluate_relevance(self, question: str, document: Document) -> RelevanceEvaluation:
        """评估单个文档相关性，失败时使用默认值兜底"""
        prompt = ChatPromptTemplate.from_template(RELEVANCE_PROMPT)
        try:
            raw_result = self._relevance_evaluator.invoke(
                prompt.format(
                    question=question,
                    document=document.page_content[:get_config("retriever.doc_truncate_length", 1000)],
                )
            )
            result = raw_result["parsed"]
            if result is None:
                raise ValueError("parsed 结果为 None")
            return result
        except Exception as e:
            logger.warning(f"相关性评估失败: error_type={type(e).__name__}")
            return RelevanceEvaluation(
                grade=RelevanceGrade.PARTIALLY_RELEVANT,
                score=0.5,
                reasoning="评估失败，使用默认值",
            )

    # ------------------------------------------------------------------
    # 生成与质量评估
    # ------------------------------------------------------------------

    def _generate_answer(self, question: str, history_text: str, context_text: str) -> str | list[Union[str, dict]]:
        """基于历史对话与检索文档生成答案"""
        prompt = ChatPromptTemplate.from_template(ANSWER_PROMPT)
        response = self._llm.invoke(
            prompt.format(
                history=history_text or "无",
                context=context_text[:get_config("agent.context_max_length", 3000)],
                question=question,
            )
        )
        return response.content

    def _evaluate_answer_quality(self, question: str, context_text: str, answer: str) -> QualityEvaluation:
        """评估答案质量，失败时使用默认值兜底"""
        prompt = ChatPromptTemplate.from_template(QUALITY_PROMPT)
        try:
            raw_result = self._quality_evaluator.invoke(
                prompt.format(
                    question=question,
                    context=context_text[:get_config("retriever.quality_context_max_length", 2000)],
                    answer=answer,
                )
            )
            result = raw_result["parsed"]
            if result is None:
                raise ValueError("parsed 结果为 None")
            logger.info(f"答案质量评估: {result.grade.value} (分数: {result.score:.2f})")
            return result
        except Exception as e:
            logger.warning(f"质量评估失败: error_type={type(e).__name__}")
            return QualityEvaluation(
                grade=QualityGrade.ACCEPTABLE,
                score=0.6,
                is_complete=True,
                is_accurate=True,
                missing_info="",
                suggestions="",
            )

    def _refine_query(self, question: str, current_info: str, missing_info: str) -> str:
        """优化检索查询，失败时沿用原始问题"""
        prompt = ChatPromptTemplate.from_template(QUERY_REFINEMENT_PROMPT)
        try:
            response = self._llm.invoke(
                prompt.format(
                    question=question,
                    current_info=current_info,
                    missing_info=missing_info,
                )
            )
            refined = (response.content or "").strip()
            logger.info(f"查询优化: {question[:30]} -> {refined[:30]}")
            return refined or question
        except Exception as e:
            logger.warning(f"查询优化失败: error_type={type(e).__name__}")
            return question

    @staticmethod
    def _should_iterate(quality: QualityEvaluation) -> bool:
        """质量不足且明确存在缺失信息时才迭代（照搬 stage_3 判断）"""
        return (
            quality.score < get_config("self_rag.quality_threshold", 0.7)
            and bool(quality.missing_info)
            and quality.grade in (
                QualityGrade.NEEDS_IMPROVEMENT,
                QualityGrade.INSUFFICIENT,
            )
        )

    # ------------------------------------------------------------------
    # 格式化工具
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 同步精检索管线（供 self_rag_search 工具使用，运行在线程池中）
    # ------------------------------------------------------------------

    def _is_sufficient(self, documents: List[Document]) -> bool:
        """判断文档是否充分（简单启发式：至少有 2 篇相关文档）"""
        return len(documents) >= 1

    def retrieve_refined(self, shop_db_id: int, query: str, question: str = "") -> List[Document]:
        """同步精检索管线：检索 → 重排序 → 相关性过滤 → 迭代优化

        只做检索+评估，不生成答案。返回经质量验证的精选文档。
        供 self_rag_search 工具和子 Agent _get_rag_context 使用。

        Args:
            shop_db_id: 店铺数据库ID（Shop表主键）
            query: 检索查询
            question: 原始问题（用于相关性评估，默认与 query 相同）

        Returns:
            经相关性过滤的精选文档列表
        """
        effective_question = question or query

        # 1. 首次检索 + 重排序
        docs = self._retrieve(shop_db_id, query)
        if not docs:
            return []
        docs = self._rerank.rerank(effective_question, docs)

        # 2. 相关性过滤
        filtered = self._filter_relevant_documents(effective_question, docs)
        if not filtered:
            return []
        # 3. 充分性检查 + 迭代优化
        seen = {doc.page_content for doc in filtered}
        current_query = query

        for iteration in range(2, get_config("self_rag.max_iterations", 3) + 1):
            if self._is_sufficient(filtered):
                break

            logger.info(f"[sync] 第 {iteration} 轮迭代检索")
            missing = f"已有 {len(filtered)} 篇相关文档，但可能不足以完整回答问题"
            current_query = self._refine_query(
                effective_question,
                format_documents(filtered)[:500],
                missing,
            )
            new_docs = self._retrieve(shop_db_id, current_query)
            added = 0
            for doc in new_docs:
                if doc.page_content not in seen:
                    seen.add(doc.page_content)
                    filtered.append(doc)
                    added += 1
            # 知识库已穷尽（没有新文档），提前退出
            if added == 0:
                logger.info(f"[sync] 第 {iteration} 轮无新文档，提前退出")
                break

        return filtered
