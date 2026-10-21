

from langchain_core.documents import Document
from loguru import logger
from typing import List, Optional, Tuple

from Agent.RAG.Retriever.bm25_retriever import BM25Retriever
from Agent.RAG.embedder import EmbeddingModel
from Agent.RAG.vector_store import VectorSearchService
from config import get_config
from core.di_container import container





class VectorRetriever:
    """向量语义检索"""
    def __init__(self, vector_store):
        self.vector_store = vector_store
        # 初始化嵌入模型
        self._embedding_model = EmbeddingModel()

    def search(self, query: str, k: int = None) -> List[Document]:
        results_with_scores = self.search_with_scores(query, k=k)
        results = [doc for doc, _ in results_with_scores]
        return results

    def search_with_scores(self, query: str, k: int = None) -> List[Tuple[Document, float]]:
        pass
        


