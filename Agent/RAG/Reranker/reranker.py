"""
重排序模块

使用阿里云百炼DashScope云端重排序服务对检索结果进行精细化重排序。
支持DashScope平台所有重排序模型，无需本地部署模型权重。
"""

from typing import List, Optional, Tuple
from loguru import logger

from langchain_core.documents import Document
from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank

from config import get_config


class Reranker:
    """
    重排序器

    对粗检索的结果进行精细化重排序，
    调用DashScope云端重排序API计算查询与文档的精确相关性分数。
    """

    # 支持的DashScope平台重排序模型列表
    SUPPORTED_MODELS = {
        "qwen3.7-text-rerank": "qwen3.7-text-rerank",
    }

    def __init__(self, model_name: str = "qwen3.7-text-rerank"):
        """
        初始化重排序器

        Args:
            model_name: DashScope平台支持的重排序模型名称
        """
        self.model_name = model_name
        self._compressor = None
        self._is_initialized = False

        logger.info(f"🔄 重排序器初始化: DashScope云端模型={model_name}")

    def _init_compressor(self):
        """懒加载DashScope重排序器实例"""
        if self._is_initialized:
            return

        try:
            # 获取DashScope平台对应的完整模型标识
            if self.model_name in self.SUPPORTED_MODELS:
                full_model_name = self.SUPPORTED_MODELS[self.model_name]
            else:
                full_model_name = self.model_name

            # 初始化云端重排序实例
            self._compressor = DashScopeRerank(
                top_n=get_config("top_k", 10),
            )
            # DashScopeRerank validator 会强制覆盖 model 为 gte-rerank，需手动修正
            self._compressor.model = full_model_name
            self._is_initialized = True

            logger.info(f"✅ DashScope重排序服务初始化完成: {full_model_name}")
        except Exception as e:
            logger.error(f"❌ 云端重排序服务初始化失败: {e}")
            raise

    def rerank(self, query: str, documents: List[Document], top_k: int = None) -> List[Tuple[Document, float]]:
        """
        对文档进行重排序
        """
        if not documents:
            return []

        top_k = top_k or get_config("top_k", 10)

        # 初始化云端重排序实例
        self._init_compressor()

        try:
            # 调用云端重排序接口
            reranked_docs = self._compressor.compress_documents(
                documents=documents,
                query=query
            )

            # 从返回的文档元数据中提取相关性分数
            doc_scores = [(doc, doc.metadata.get("relevance_score", 0.0)) for doc in reranked_docs]

            # 按分数降序二次校验排序
            doc_scores.sort(key=lambda x: x[1], reverse=True)

            docs = [doc for doc, _score in doc_scores]

            # 返回指定数量的Top-K结果
            result = docs[:top_k]
            logger.info(f"🔄 云端重排序完成: {len(documents)} -> {len(result)} 个文档, 最高分={doc_scores[0][1]:.4f}")

            return result
        except Exception as e:
            logger.error(f"❌ 云端重排序失败: {e}")
            # 接口调用失败时返回原始顺序，兼容原有降级逻辑
            return [(doc, 0.0) for doc in documents[:top_k]]
    
    def rerank_and_filter(
        self,
        query: str,
        documents: List[Document],
        top_k: int = None,
        threshold: float = 0.0
    ) -> List[Document]:
        """
        重排序并过滤低相关文档
        
        Args:
            query: 查询文本
            documents: 待排序的文档列表
            top_k: 返回的结果数量
            threshold: 分数阈值，低于此分数的文档将被过滤
            
        Returns:
            List[Document]: 排序并过滤后的文档列表
        """
        reranked = self.rerank(query, documents, top_k=len(documents))
        
        # 过滤低分文档
        filtered = [
            doc for doc, score in reranked 
            if score >= threshold
        ]
        
        # 限制返回数量
        top_k = top_k or self.config.top_k
        result = filtered[:top_k]
        
        if len(filtered) < len(reranked):
            logger.info(f"🔍 过滤低相关文档: {len(reranked)} -> {len(filtered)} -> {len(result)}")
        
        return result


class SimpleReranker:
    """
    简单重排序器（不依赖外部模型）

    使用基于规则的方法进行重排序，适用于无法加载模型的情况。
    """

    def __init__(self):
        """初始化简单重排序器"""
        logger.info("🔄 简单重排序器初始化完成")

    def rerank(self, query: str, documents: List[Document], top_k: int = None) -> List[Tuple[Document, float]]:
        """
        基于关键词匹配的简单重排序

        Args:
            query: 查询文本
            documents: 待排序的文档列表
            top_k: 返回的结果数量

        Returns:
            List[Tuple[Document, float]]: (文档, 分数) 列表
        """
        if not documents:
            return []

        top_k = top_k or get_config("top_k", 10)
        query_terms = set(query.lower().split())

        scored_docs = []
        for doc in documents:
            content_lower = doc.page_content.lower()

            # 计算匹配的查询词数量
            matches = sum(1 for term in query_terms if term in content_lower)

            # 考虑词频
            frequency = sum(content_lower.count(term) for term in query_terms)

            # 综合分数
            score = matches * 2 + frequency * 0.1
            scored_docs.append((doc, score))

        # 排序
        scored_docs.sort(key=lambda x: x[1], reverse=True)

        return scored_docs[:top_k]
