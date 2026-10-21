"""
Agent/AgenticRAGAgent 向量相似度检索服务
========================================

参照 UltimateRAG stage_1/vectorstore.py（Chroma 持久化）。

- Embedding: langchain_openai.OpenAIEmbeddings，复用 config.json 的
  llm.api_key/api_base（OpenAI 兼容端点，DashScope text-embedding-v4）
- 向量库: Chroma 持久化，每个店铺三个 collection：
    shop_{db_id}_products  —— 产品知识
    shop_{db_id}_cs        —— 客服知识
    shop_{db_id}_docs      —— 上传知识文档
- 索引同步: 检索时对比 Chroma 文档数与 SQLite 知识数，
  不一致自动重建；另提供手动 rebuild（数量校验策略）
"""
from __future__ import annotations

import json
import time
from typing import List, Optional, Dict

from Agent.RAG.VectorStore.vector_store_chroma import VectorStoreChroma
import utils.onnx_stub  # noqa: F401  桩掉 chromadb 默认 ONNX 嵌入函数
import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

from Agent.utils import to_documents
from config import get_config
from database.db_manager import get_db_manager
from database.knowledge_service import KnowledgeService
from utils.logger_loguru import get_logger
from core.di_container import container, injectable, ServiceLifetime

logger = get_logger("AgenticRAGAgent.vector_store")


def _patch_chroma_chinese_metadata() -> None:
    """运行时补丁：让 Chroma 内部 WAL（embeddings_queue 表）的 metadata 存中文原文

    不修改 chromadb 库文件，仅在进程内替换其序列化方法：
     chromadb 原实现 json.dumps(metadata) 默认 ensure_ascii=True，
    会把中文转成 \\uXXXX，直接打开 sqlite 查看时不可读；
    改为 ensure_ascii=False 后按 UTF-8 原文存储，读取/回放完全兼容。

    注意：metadata 中的内部键 chroma:document 不能剔除——
    WAL 没有独立文档列，崩溃恢复时全靠它还原文档全文。
    """
    from chromadb.db.mixins import embeddings_queue as _eq
    from chromadb.ingest import encode_vector

    if getattr(_eq.SqlEmbeddingsQueue, "_chinese_metadata_patched", False):
        return

    def _prepare_vector_encoding_metadata(self, embedding: dict) -> tuple:
        """替换 chromadb 同名方法：序列化 metadata 时保留中文（签名与原方法一致）"""
        embedding_bytes = None
        encoding = None
        if embedding["embedding"] is not None:
            encoding = embedding["encoding"].value
            embedding_bytes = encode_vector(
                embedding["embedding"], embedding["encoding"]
            )
        metadata = (
            json.dumps(embedding["metadata"], ensure_ascii=False)
            if embedding["metadata"]
            else None
        )
        return embedding_bytes, encoding, metadata

    _eq.SqlEmbeddingsQueue._prepare_vector_encoding_metadata = (
        _prepare_vector_encoding_metadata
    )
    _eq.SqlEmbeddingsQueue._chinese_metadata_patched = True
    logger.debug("已应用 Chroma 中文 metadata 补丁（ensure_ascii=False）")


# 必须在创建 PersistentClient 之前完成补丁
_patch_chroma_chinese_metadata()

@injectable(lifetime=ServiceLifetime.SINGLETON)
class VectorSearchService:
    """向量相似度检索服务（每店铺三个 Chroma collection：products / cs / docs）"""

    def __init__(self):
        """
        Args:
            knowledge_service: 知识库服务，用于加载索引数据与解析店铺 ID
        """
        self._ks = container.get(KnowledgeService)
        self._persist_dir = get_config("embedding.persist_dir", "./temp/chroma")
        self._client = chromadb.PersistentClient(path=self._persist_dir)
        # 重建失败冷却表：{db_shop_id: 上次失败时间戳}，避免每条消息都全量重试
        self._build_failed_at: dict = {}
        self._vector_store_list : Dict[str, VectorStoreChroma] = {}
        logger.info(f"VectorSearchService 初始化成功: persist_dir={self._persist_dir}")

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def search(self, shop_id: int, query: str, top_k: int = 5) -> List[Document]:
        """向量相似度检索；索引与 SQLite 不一致时自动重建

        检索范围 = 产品集合 + 客服集合 + 知识文档集合，
        按 page_content 合并去重。

        Args:
            shop_id: 店铺原始 ID
            query: 查询文本
            top_k: 每个集合返回条数
        """
        db_id = get_db_manager().resolve_shop_db_id(shop_id)
        if db_id is None:
            return []
        merged: List[Document] = []
        seen: set = set()
        # 产品知识集合
        try:
            products_collection_name = self._products_collection_name(db_id)
            store = self._get_store(products_collection_name)
            if store is not None:
                for doc in store.similarity_search(query, k=top_k):
                    if doc.page_content not in seen:
                        seen.add(doc.page_content)
                        merged.append(doc)
        except Exception as e:
            logger.warning(f"产品知识向量检索失败: error_type={type(e).__name__}, detail={str(e)[:200]}")
        # 客服知识集合
        try:
            cs_collection_name = self._cs_collection_name(db_id)
            store = self._get_store(cs_collection_name)
            if store is not None:
                for doc in store.similarity_search(query, k=top_k):
                    if doc.page_content not in seen:
                        seen.add(doc.page_content)
                        merged.append(doc)
        except Exception as e:
            logger.warning(
                f"客服知识向量检索失败: error_type={type(e).__name__}, detail={str(e)[:200]}"
            )
        # 上传的知识文档（独立集合，metadata 过滤启用状态）
        for doc in self.search_documents(db_id, query, top_k):
            if doc.page_content not in seen:
                seen.add(doc.page_content)
                merged.append(doc)
        return merged

    def rebuild_product_index(self, db_id: int) -> int:
        """重建产品知识向量索引，返回索引的文档数"""
        docs = self._load_product_documents(db_id)
        self._rebuild_collection(db_id, "products", docs)
        self._build_failed_at.pop((db_id, "products"), None)
        return len(docs)

    def rebuild_cs_index(self, db_id: int) -> int:
        """重建客服知识向量索引，返回索引的文档数"""
        docs = self._load_cs_documents(db_id)
        self._rebuild_collection(db_id, "cs", docs)
        self._build_failed_at.pop((db_id, "cs"), None)
        return len(docs)

    # ------------------------------------------------------------------
    # 知识文档：独立 collection（shop_{db_id}_docs）
    # ------------------------------------------------------------------

    @staticmethod
    def _docs_collection_name(db_id: int) -> str:
        return f"shop_{db_id}_docs"

    def add_document_chunks(self, db_id: int, doc_id: int, chunks: List[Document],) -> int:
        """把文档分块写入店铺文档向量集合
        chunk 元数据：doc_id / file_name / chunk_index / enabled / source
        """
        docs_collection_name = self._docs_collection_name(db_id)
        store = self._get_store(docs_collection_name)
        try:
            store._add_in_batches(chunks)
        except Exception as e:
            logger.warning(f"文档向量写入失败: error_type={type(e).__name__}, detail={str(e)[:200]}")
            # 分批写入中途失败：清理已写入的半成品 chunk，避免留下孤儿向量
            store.delete_document_chunks(doc_id)
            raise
        logger.info(f"文档向量写入完成: db_shop_id={db_id}, doc_id={doc_id}, chunks={len(chunks)}")
        return len(chunks)

    def set_document_enabled(self, db_id: int, doc_id: int, enabled: bool) -> None:
        """启停文档：只更新 chunk 元数据，不重新 embedding"""
        docs_collection_name = self._docs_collection_name(db_id)
        store = self._get_store(docs_collection_name)
        store.set_document_enabled(doc_id, enabled)

    def delete_document_chunks(self, db_id: int, doc_id: int) -> None:
        """删除文档全部 chunk 向量
    
        注意：Chroma 0.6.x 的 collection.delete(where=...) 存在静默失败问题，
        需先 get(where=...) 拿到 ID 列表，再按 ID 删除。
        """
        docs_collection_name = self._docs_collection_name(db_id)
        store = self._get_store(docs_collection_name)
        store.delete_document_chunks(doc_id)
        logger.info(f"文档向量删除完成: db_id={db_id}, doc_id={doc_id}")

    def search_documents(self, db_id: int, query: str, top_k: int = 5) -> List[Document]:
        """检索店铺文档集合，仅返回启用中文档的 chunk"""
        try:
            docs_collection_name = self._docs_collection_name(db_id)
            store = self._get_store(docs_collection_name)
            return store.similarity_search(query, k=top_k, filter={"enabled": "true"})
        except Exception as e:
            logger.warning(f"文档向量检索失败: error_type={type(e).__name__}, detail={str(e)[:200]}")
            return []

    def get_all_documents(self, db_id: int) -> List[Document]:
        docs_collection_name = self._docs_collection_name(db_id)
        store = self._get_store(docs_collection_name)
        documents = store.get_all_documents()
        return documents

    def peek_documents(self, shop_id: int, limit: int = 50) -> List[dict]:
        """离线预览店铺文档集合的分块内容（直接读已存原文，不调用 embedding API）

        返回中文字典列表：[{file_name, chunk_index, enabled, content}]，
        按文件名、分块序号排序，供人工核对入库内容（避免 JSON Unicode 转义）。
        """
        db_id = get_db_manager().resolve_shop_db_id(shop_id)
        if db_id is None:
            return []
        collection = self._client.get_or_create_collection(
            self._docs_collection_name(db_id)
        )
        data = collection.get(include=["documents", "metadatas"], limit=limit)
        items: List[dict] = []
        for content, meta in zip(
            data.get("documents", []), data.get("metadatas", [])
        ):
            meta = meta or {}
            items.append({
                "file_name": meta.get("file_name", ""),
                "chunk_index": meta.get("chunk_index", 0),
                "enabled": meta.get("enabled", "true"),
                "content": content or "",
            })
        items.sort(
            key=lambda item: (
                item["file_name"],
                item["chunk_index"] if isinstance(item["chunk_index"], int) else 0,
            )
        )
        return items


    # ------------------------------------------------------------------
    # 产品知识：独立 collection（shop_{db_id}_products）
    # ------------------------------------------------------------------

    @staticmethod
    def _products_collection_name(db_id: int) -> str:
        return f"shop_{db_id}_products"

    def add_products_data(self, db_id: int, doc_id: int, data) -> int:
        """把文档分块写入店铺文档向量集合
        chunk 元数据：doc_id / file_name / chunk_index / enabled / source
        """
        chunks = to_documents([data], [])
        return self.add_products_chunks(db_id, doc_id, chunks)

    def add_products_chunks(self, db_id: int, doc_id: int, chunks: List[Document],) -> int:
        """把文档分块写入店铺文档向量集合
        chunk 元数据：doc_id / file_name / chunk_index / enabled / source
        """
        products_collection_name = self._products_collection_name(db_id)
        store = self._get_store(products_collection_name)
        try:
            store._add_in_batches(chunks)
        except Exception:
            # 分批写入中途失败：清理已写入的半成品 chunk，避免留下孤儿向量
            store.delete_document_chunks(doc_id)
            raise
        logger.info(f"文档向量写入完成: db_shop_id={db_id}, doc_id={doc_id}, chunks={len(chunks)}")
        return len(chunks)

    def _load_product_documents(self, db_id: int) -> List[Document]:
        """从 SQLite 加载产品知识并转为 Document"""
        products = self._ks.list_products_by_shop(db_id)
        return to_documents(products, [])

    # ------------------------------------------------------------------
    # 客服知识：独立 collection（shop_{db_id}_products）
    # ------------------------------------------------------------------
    @staticmethod
    def _cs_collection_name(db_id: int) -> str:
        return f"shop_{db_id}_cs"

    def add_cs_data(self, db_id: int, doc_id: int, data) -> int:
        """把文档分块写入店铺文档向量集合
        chunk 元数据：doc_id / file_name / chunk_index / enabled / source
        """
        chunks = to_documents([], [data])
        return self.add_cs_chunks(db_id, doc_id, chunks)

    def add_cs_chunks(self, db_id: int, doc_id: int, chunks: List[Document],) -> int:
        """把文档分块写入店铺文档向量集合
        chunk 元数据：doc_id / file_name / chunk_index / enabled / source
        """
        products_collection_name = self._products_collection_name(db_id)
        store = self._get_store(products_collection_name)
        try:
            store._add_in_batches(chunks)
        except Exception:
            # 分批写入中途失败：清理已写入的半成品 chunk，避免留下孤儿向量
            store.delete_document_chunks(doc_id)
            raise
        logger.info(f"文档向量写入完成: db_shop_id={db_id}, doc_id={doc_id}, chunks={len(chunks)}")
        return len(chunks)

    def _load_cs_documents(self, db_id: int) -> List[Document]:
        """从 SQLite 加载客服知识并转为 Document（仅启用中）"""
        cs_list = self._ks.list_customer_service_by_shop(db_id)
        return to_documents([], cs_list)


    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def get_store(self, db_id: int, suffix: str) -> VectorStoreChroma:
        collection_name = f"shop_{db_id}_{suffix}"
        return self._get_store(collection_name)

    def _get_store(self, collection_name: str) -> VectorStoreChroma:
        """获取产品知识向量库"""
        store = self._vector_store_list.get(collection_name, None)
        if store is None:
            store = VectorStoreChroma(collection_name)
            self._vector_store_list[collection_name] = store
        return store

    def _clear_collection(self, db_id: int, suffix: str) -> None:
        """通用集合清除：先按 ID 批量删除数据，再删除集合本身

        Chroma 0.6.x 的 delete_collection 可能不会立即持久化，
        因此先按 ID 逐批清空数据，再尝试删除集合本身。
        """
        collection_name = f"shop_{db_id}_{suffix}"
        try:
            collection = self._client.get_or_create_collection(collection_name)
            all_ids = collection.get()["ids"]
            if all_ids:
                batch_size = 100
                for i in range(0, len(all_ids), batch_size):
                    collection.delete(ids=all_ids[i:i + batch_size])
            self._client.delete_collection(collection_name)
            logger.info(
                f"向量集合已清除: collection={collection_name}, deleted={len(all_ids)}"
            )
        except Exception as e:
            logger.warning(
                f"向量集合清除失败: collection={collection_name}, "
                f"error_type={type(e).__name__}"
            )
        # 清理旧版合并集合（shop_{db_id}），迁移到拆分架构
        self._migrate_old_collection(db_id)

    def _migrate_old_collection(self, db_id: int) -> None:
        """删除旧版合并集合 shop_{db_id}，释放拆分后不再使用的存储空间"""
        old_name = f"shop_{db_id}"
        try:
            self._client.delete_collection(old_name)
            logger.info(f"已清理旧版合并集合: {old_name}")
        except Exception:
            pass  # 不存在时忽略

    def clear_product_vectors(self, db_id: int) -> None:
        """删除店铺产品知识的全部向量集合"""
        self._clear_collection(db_id, "products")

    def clear_cs_vectors(self, db_id: int) -> None:
        """删除店铺客服知识的全部向量集合"""
        self._clear_collection(db_id, "cs")

    def reset_doc_vectors(self, db_id: int) -> None:
        collection_name = self._docs_collection_name(db_id)
        store = self._get_store(collection_name)
        store.reset_collection()

    def clear_doc_vectors(self, db_id: int) -> None:
        """删除店铺知识文档的全部向量集合（shop_{db_id}_docs）

        Chroma 0.6.x 的 delete_collection 可能不会立即持久化，
        因此先按 ID 逐批清空数据，再尝试删除集合本身。
        """
        collection_name = self._docs_collection_name(db_id)
        try:
            collection = self._client.get_or_create_collection(collection_name)
            all_ids = collection.get()["ids"]
            if all_ids:
                batch_size = 100
                for i in range(0, len(all_ids), batch_size):
                    collection.delete(ids=all_ids[i:i + batch_size])
            self._client.delete_collection(collection_name)
            logger.info(
                f"文档向量集合已清除: collection={collection_name}, "
                f"deleted={len(all_ids)}"
            )
        except Exception as e:
            logger.warning(
                f"文档向量集合清除失败: collection={collection_name}, "
                f"error_type={type(e).__name__}"
            )

    def _product_collection_count(self, db_id: int) -> int:
        """读取产品向量集合文档数，失败时返回 -1"""
        try:
            return self._client.get_or_create_collection(
                self._products_collection_name(db_id)
            ).count()
        except Exception as e:
            logger.warning(f"读取产品向量集合失败: error_type={type(e).__name__}")
            return -1

    def _cs_collection_count(self, db_id: int) -> int:
        """读取客服向量集合文档数，失败时返回 -1"""
        try:
            return self._client.get_or_create_collection(
                self._cs_collection_name(db_id)
            ).count()
        except Exception as e:
            logger.warning(f"读取客服向量集合失败: error_type={type(e).__name__}")
            return -1


    def _ensure_index_impl(
        self,
        db_id: int,
        kind: str,
        load_fn,
        count_fn,
        get_store_fn,
    ) -> Optional[Chroma]:
        """通用索引校验：Chroma 文档数与 SQLite 不一致时自动重建"""
        docs = load_fn(db_id)
        if not docs:
            return None
        count = count_fn(db_id)
        if count == len(docs):
            return get_store_fn(db_id)

        cooldown_key = (db_id, kind)
        elapsed = time.time() - self._build_failed_at.get(cooldown_key, 0)
        cooldown = get_config("embedding.rebuild_cooldown", 300)
        if elapsed < cooldown:
            left = int(cooldown - elapsed)
            logger.warning(f"{kind}向量索引过期且上次重建失败，{left} 秒内不重试: db_shop_id={db_id}")
            return get_store_fn(db_id)

        logger.info(f"{kind}向量索引过期（Chroma={count}, SQLite={len(docs)}），自动重建: db_shop_id={db_id}")
        try:
            self._rebuild_collection(db_id, kind, docs)
            self._build_failed_at.pop(cooldown_key, None)
            return get_store_fn(db_id)
        except Exception as e:
            self._build_failed_at[cooldown_key] = time.time()
            logger.error(
                f"{kind}向量索引重建失败，{get_config('embedding.rebuild_cooldown', 300)} 秒内降级旧索引: "
                f"db_shop_id={db_id}, error_type={type(e).__name__}, "
                f"detail={str(e)[:300]}"
            )
            return get_store_fn(db_id)

    def _rebuild_collection(self, db_id: int, kind: str, docs: List[Document]) -> Optional[VectorStoreChroma]:
        """原子重建指定集合：先写临时集合，成功后替换

        写入失败时旧集合保持不动（不会出现"先删后写失败导致索引清空"）。
        """
        final_name = f"shop_{db_id}_{kind}"
        store = self._get_store(final_name)
        store.rebuild_collection(docs)
        return store

