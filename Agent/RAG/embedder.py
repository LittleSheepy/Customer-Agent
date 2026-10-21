"""
嵌入模型模块

封装 Embedding 模型，支持 OpenAI 和本地模型。
用于将文本转换为向量表示。
"""

from typing import List, Optional
from loguru import logger

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from config import get_config


# 阿里云 Embedding API 的批量大小限制
ALIYUN_EMBEDDING_BATCH_SIZE = 2


class BatchedEmbeddings(Embeddings):
    """
    批量嵌入包装器
    
    用于解决阿里云等 API 提供商的批量大小限制问题。
    将大批量文本拆分成多个小批次分别调用，然后合并结果。
    
    阿里云兼容 OpenAI 的 Embedding 接口限制每次请求最多 10 条文本。
    """
    
    def __init__(self, inner: Embeddings, batch_size: int = ALIYUN_EMBEDDING_BATCH_SIZE):
        """
        初始化批量嵌入包装器
        
        Args:
            inner: 内部的嵌入模型实例（如 OpenAIEmbeddings）
            batch_size: 每批次最大文本数量，默认 10（阿里云限制）
        """
        self.inner = inner
        self.batch_size = batch_size
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量嵌入文档（分批调用）
        
        Args:
            texts: 文本列表
            
        Returns:
            List[List[float]]: 向量列表
        """
        if not texts:
            return []
        
        all_embeddings: List[List[float]] = []
        total_batches = (len(texts) + self.batch_size - 1) // self.batch_size
        
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            current_batch = i // self.batch_size + 1
            
            logger.debug(f"📦 嵌入批次 {current_batch}/{total_batches}: 处理 {len(batch)} 条文本")
            
            # 调用内部嵌入模型
            batch_embeddings = self.inner.embed_documents(batch)
            all_embeddings.extend(batch_embeddings)
        
        logger.debug(f"✅ 批量嵌入完成: 共 {len(texts)} 条文本，分 {total_batches} 批处理")
        return all_embeddings
    
    def embed_query(self, text: str) -> List[float]:
        """
        嵌入单条查询（直接调用内部模型）
        
        Args:
            text: 查询文本
            
        Returns:
            List[float]: 查询向量
        """
        # 查询只有一条，不需要分批，直接透传
        return self.inner.embed_query(text)


class EmbeddingModel:
    """
    嵌入模型封装类
    
    支持 OpenAI Embeddings 和本地 HuggingFace 模型。
    Phase 1 默认使用 OpenAI text-embedding-3-small。
    """
    
    def __init__(self):
        """
        初始化嵌入模型
        """
        self._embeddings: Optional[Embeddings] = None
        
    @property
    def embeddings(self) -> Embeddings:
        """
        获取嵌入模型实例（懒加载）
        
        Returns:
            Embeddings: LangChain 嵌入模型实例
        """
        if self._embeddings is None:
            self._embeddings = self._create_embeddings()
        return self._embeddings
    
    def _create_embeddings(self) -> Embeddings:
        """
        创建嵌入模型实例
        
        使用 BatchedEmbeddings 包装器来解决阿里云等 API 的批量大小限制。
        
        Returns:
            Embeddings: LangChain 嵌入模型实例（已包装分批处理）
        """
        model_name = get_config("llm.embedding_model_name", "")
        
        # 使用 OpenAI Embeddings
        kwargs = {
            "model": get_config("llm.embedding_model_name", ""),
            "api_key": get_config("llm.api_key", ""),
            "base_url": get_config("llm.api_base", ""),
            "check_embedding_ctx_length": False,  # 阿里云兼容模式需要关闭
        }

        base_embeddings = OpenAIEmbeddings(**kwargs)
        
        # 使用 BatchedEmbeddings 包装，自动分批处理（每批最多 10 条）
        embeddings = BatchedEmbeddings(inner=base_embeddings, batch_size=ALIYUN_EMBEDDING_BATCH_SIZE)
        
        logger.info(f"🔢 嵌入模型初始化完成: {model_name} (批量大小: {ALIYUN_EMBEDDING_BATCH_SIZE})")
        return embeddings
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量嵌入文档
        
        Args:
            texts: 文本列表
            
        Returns:
            List[List[float]]: 向量列表
        """
        if not texts:
            return []
        
        vectors = self.embeddings.embed_documents(texts)
        logger.debug(f"✅ 已嵌入 {len(texts)} 个文档，向量维度: {len(vectors[0])}")
        return vectors
    
    def embed_query(self, text: str) -> List[float]:
        """
        嵌入查询文本
        
        Args:
            text: 查询文本
            
        Returns:
            List[float]: 查询向量
        """
        vector = self.embeddings.embed_query(text)
        logger.debug(f"✅ 已嵌入查询，向量维度: {len(vector)}")
        return vector
