"""
混合检索模块

实现 BM25 关键词检索 + 向量检索的混合策略。
能够同时处理语义匹配和关键词精确匹配。
"""

from langchain_core.documents import Document
from loguru import logger
from typing import List, Optional, Tuple

from Agent.RAG.Retriever.bm25_retriever import BM25Retriever
from Agent.RAG.vector_store import VectorSearchService
from config import get_config
from core.di_container import injectable, ServiceLifetime, container


@injectable()
class HybridRetriever:
    """
    混合检索器
    
    结合 BM25 关键词检索和向量语义检索的优势。
    使用倒排融合（RRF）算法合并结果。
    """
    
    def __init__(self, account_info=None, documents: List[Document] = None, bm25_weight: float = 0.5, vector_weight: float = 0.5):
        """
        初始化混合检索器
        
        Args:
            documents: 文档列表（用于构建 BM25 索引）
            bm25_weight: BM25 结果权重
            vector_weight: 向量检索结果权重
        """
        self.account_info = account_info
        self._vector : VectorSearchService = container.get(VectorSearchService)
        self.bm25_weight = bm25_weight
        self.vector_weight = vector_weight
        if documents is None:
            documents = self._vector.get_all_documents(self.account_info.get("id"))
        self._bm25_retriever = BM25Retriever(documents)
        
        logger.info(f"🔀 混合检索器初始化: BM25权重={bm25_weight}, 向量权重={vector_weight}")

    
    def vector_search(self, query: str, k: int = 10) -> List[Tuple[Document, float]]:
        """
        向量语义检索
        
        Args:
            query: 查询文本
            k: 返回结果数量
            
        Returns:
            List[Tuple[Document, float]]: (文档, 分数) 列表
        """
        results = self._vector.similarity_search_with_score(query, k=k)
        
        # ChromaDB 返回的是距离，需要转换为相似度分数
        # 距离越小，相似度越高
        processed = []
        for doc, distance in results:
            # 将距离转换为 0-1 的相似度分数
            similarity = 1 / (1 + distance)
            processed.append((doc, similarity))
        
        return processed
    
    def _reciprocal_rank_fusion(self, result_lists: List[List[Tuple[Document, float]]], k: int = 60
    ) -> List[Tuple[Document, float]]:
        """
        倒排融合算法 (Reciprocal Rank Fusion)
        
        Args:
            result_lists: 多个检索结果列表
            k: RRF 参数（防止排名过于集中）
            
        Returns:
            List[Tuple[Document, float]]: 融合后的结果列表
        """
        # 使用文档内容作为唯一标识
        doc_scores = {}
        doc_map = {}
        
        for results in result_lists:
            for rank, (doc, _) in enumerate(results):
                doc_key = hash(doc.page_content)
                
                if doc_key not in doc_scores:
                    doc_scores[doc_key] = 0
                    doc_map[doc_key] = doc
                
                # RRF 公式: 1 / (k + rank)
                doc_scores[doc_key] += 1 / (k + rank + 1)
        
        # 按分数排序
        sorted_keys = sorted(doc_scores.keys(), key=lambda x: doc_scores[x], reverse=True)
        
        return [(doc_map[key], doc_scores[key]) for key in sorted_keys]
    
    def search(self, query: str, k: int = None) -> List[Document]:
        """
        混合检索
        
        Args:
            query: 查询文本
            k: 返回结果数量
            
        Returns:
            List[Document]: 检索结果
        """
        results_with_scores = self.search_with_scores(query, k=k)

        results = [doc for doc, _ in results_with_scores]
        
        return results
    
    def search_with_scores(self, query: str, k: int = None) -> List[Tuple[Document, float]]:
        """
        混合检索（带分数）
        
        Args:
            query: 查询文本
            k: 返回结果数量
            
        Returns:
            List[Tuple[Document, float]]: (文档, 分数) 列表
        """
        k = k or get_config("top_k")

        # 检索更多结果用于融合
        search_k = k * 3

        # BM25 检索
        bm25_results = self._bm25_retriever.search_with_scores(query, k=search_k)

        doc_store = self._vector.get_store(self.account_info.get("id"), "docs")
        vector_results = doc_store.similarity_search_with_score(query, k=search_k)
        products_store = self._vector.get_store(self.account_info.get("id"), "products")
        products_vector_results = products_store.similarity_search_with_score(query, k=search_k)
        cs_store = self._vector.get_store(self.account_info.get("id"), "cs")
        cs_vector_results = cs_store.similarity_search_with_score(query, k=search_k)

        # vector_results = self.vector_search(query, k=search_k)
        
        if bm25_results and vector_results:
            weighted_bm25 = [(doc, score * self.bm25_weight) for doc, score in bm25_results]
            weighted_vector = [(doc, score * self.vector_weight) for doc, score in vector_results]
            fused = self._reciprocal_rank_fusion([weighted_bm25, weighted_vector])
        elif bm25_results:
            fused = bm25_results
        elif vector_results:
            fused = vector_results
        else:
            fused = []

        results = fused[:k]

        results = results + products_vector_results + cs_vector_results
        logger.info(f"🔍 混合检索完成: BM25={len(bm25_results)}, doc向量={len(vector_results)}, 融合后={len(fused)}"
                    f"products向量={len(products_vector_results)}, "
                    f"cs向量={len(cs_vector_results)}, "
                    f"汇总后={len(results)}")
        return results
