"""
Agent/AgenticRAGAgent 知识文档向量化服务
=========================================

参照 UltimateRAG src/stage_1 的文档向量化流程：

    选择文件 -> 加载文档（PDF/DOCX/TXT/MD）-> 中文友好分块
    -> Embedding -> 写入店铺独立 Chroma 集合（shop_{id}_docs）
    -> SQLite 登记文档记录

同时提供启停（只改 chunk 元数据）与删除（向量 + 原件 + DB 记录）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from Agent.RAG.document_loader import DocumentLoader
from Agent.RAG.vector_store import VectorSearchService
from Agent.RAG.Chunker.chunker import TextChunker
from Agent.RAG.Chunker.semantic_chunker import SemanticChunker
from config import get_config
from core import container
from core.di_container import injectable, ServiceLifetime
from database.knowledge_service import KnowledgeService
from database.models import KnowledgeDocument
from utils.logger_loguru import get_logger

logger = get_logger("AgenticRAGAgent.document_service")

# 单文件添加结果：(是否成功, 文件名, 说明)
AddResult = Tuple[bool, str, str]


def file_md5(file_path: str) -> str:
    """计算文件 MD5（用于同店铺去重）"""
    digest = hashlib.md5()
    with open(file_path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            digest.update(block)
    return digest.hexdigest()

@injectable(lifetime=ServiceLifetime.SINGLETON)
class DocumentIndexService:
    """知识文档向量化编排：文件保存、加载分块、向量写入、DB 登记"""

    def __init__(self):
        self._ks = container.get(KnowledgeService)
        self._vector = container.get(VectorSearchService)
        self._loader = DocumentLoader()
        if get_config("chunker.active") == "semantic":
            self._chunker = SemanticChunker()
        else:
            self._chunker = TextChunker(
                chunk_size=get_config("chunker.chunk_size", 256),
                chunk_overlap=get_config("chunker.chunk_overlap", 50),
            )
    # ------------------------------------------------------------------
    # 添加
    # ------------------------------------------------------------------

    def add_file(self, shop_db_id: int, file_path: str) -> None | KnowledgeDocument:
        """单个文档：校验 -> 去重 -> 保存原件 -> 加载分块 -> 向量化 -> 登记

        Raises:
            ValueError: 格式不支持 / 空文档
            FileExistsError: 同店铺已存在相同文件
        """
        path = Path(file_path)
        supported = get_config("chunker.supported_suffixes", [".pdf", ".docx", ".txt", ".md"])
        if path.suffix.lower() not in supported:
            raise ValueError(f"不支持的文件格式: {path.suffix}")

        file_hash = file_md5(str(path))
        if self._ks.get_document_by_hash(shop_db_id, file_hash) is not None:
            raise FileExistsError(f"文档已存在，无需重复添加: {path.name}")

        # 保存原件到项目目录（hash 前缀防重名）
        stored_path = self._store_file(shop_db_id, file_hash, path)

        try:
            self.load_and_index_documents(shop_db_id, file_path, stored_path, file_hash)
        except Exception:
            # 加载/分块失败：清理已保存的原件
            self._remove_file(stored_path)
            raise

    def add_files(
        self,
        shop_db_id: int,
        file_paths: List[str],
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[AddResult]:
        """批量添加，逐个处理，单个失败不影响其他文件

        Args:
            progress_cb: 进度回调 (已完成数, 总数, 当前文件名)
        """
        results: List[AddResult] = []
        total = len(file_paths)
        for index, file_path in enumerate(file_paths, 1):
            name = Path(file_path).name
            if progress_cb:
                progress_cb(index - 1, total, name)
            try:
                self.add_file(shop_db_id, file_path)
                results.append((True, name, ""))
            except FileExistsError as e:
                results.append((False, name, f"跳过（重复）: {e}"))
            except Exception as e:
                results.append((False, name, f"失败: {type(e).__name__}: {e}"))
            if progress_cb:
                progress_cb(index, total, name)
        return results

    # ------------------------------------------------------------------
    # 启停 / 删除
    # ------------------------------------------------------------------

    def set_enabled(self, doc_id: int, enabled: bool) -> bool:
        """启用/禁用：DB 状态 + 向量元数据同步更新"""
        doc : KnowledgeDocument = self._ks.get_document_by_id(doc_id)
        if doc is None:
            return False
        if not self._ks.set_document_enabled(doc_id, enabled):
            return False
        self._vector.set_document_enabled(doc.shop_db_id, doc.id, enabled)
        return True

    def delete_document(self, doc_id: int) -> bool:
        """删除：DB 记录 -> 向量 chunk -> 原件（后两者尽力清理）"""
        deleted = self._ks.delete_document(doc_id)
        if deleted is None:
            return False
        try:
            self._vector.delete_document_chunks(deleted.shop_db_id, deleted.id)
        except Exception as e:
            logger.warning(f"文档向量清理失败: doc_id={doc_id}, {type(e).__name__}:{e}")
        self._remove_file(deleted.stored_path)
        return True

    def delete_all_document(self, shop_id: int) -> bool:
        """删除：DB 记录 -> 向量 chunk -> 原件（后两者尽力清理）"""
        deleted_list = self._ks.delete_documents_by_shop(shop_id)
        if deleted_list is None:
            return False
        try:
            self._vector.reset_doc_vectors(shop_id)
        except Exception as e:
            logger.warning(f"文档向量清理失败: shop_id={shop_id}, {type(e).__name__}:{e}")

        for deleted in deleted_list:
            self._remove_file(deleted.stored_path)
        return True

    # ------------------------------------------------------------------
    # 文件存储
    # ------------------------------------------------------------------

    @staticmethod
    def _store_file(shop_db_id: int, file_hash: str, path: Path) -> str:
        """复制原件到 chunker.store_dir/shop_{id}/，返回保存后的绝对路径字符串"""
        store_dir = get_config("chunker.store_dir", "./temp/knowledge_docs")
        target_dir = Path(store_dir) / f"shop_{shop_db_id}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{file_hash[:12]}_{path.name}"
        shutil.copy2(str(path), str(target))
        return str(target)

    @staticmethod
    def _remove_file(stored_path: str) -> None:
        """尽力删除保存的原件，失败只记日志"""
        try:
            if stored_path and os.path.exists(stored_path):
                os.remove(stored_path)
        except Exception as e:
            logger.warning(f"原件删除失败: path={stored_path}, {type(e).__name__}:{e}")

    # ------------------------------------------------------------------
    # 文件向量化
    # ------------------------------------------------------------------

    def load_and_index_documents(
            self,
            shop_db_id,
            file_path: str | Path,
            stored_path,
            file_hash,
    ) -> bool:
        """
        加载并索引文档
        """
        # 1. 加载文档
        logger.info("📄 开始加载文档...")
        documents = self._loader.load(file_path)
        if not documents:
            logger.warning("⚠️ 没有找到任何文档")
            return None

        # 2. 分块处理
        logger.info("✂️ 开始分块处理...")
        chunks = self._chunker.split_documents(documents)
        if not chunks:
            logger.warning("⚠️ 文档内容为空，无法向量化")
            raise None

        # 先登记 DB 拿到 doc_id，再写入向量（元数据需要 doc_id）
        doc = self._ks.add_document(
            shop_db_id=shop_db_id,
            file_name=str(file_path),
            stored_path=stored_path,
            file_hash=file_hash,
            chunk_count=len(chunks),
        )
        try:
            for index, chunk in enumerate(chunks):
                chunk.metadata = {
                    **(chunk.metadata or {}),
                    "doc_id": str(doc.id),
                    "file_name": str(file_path),
                    "chunk_index": index,
                    "enabled": "true",
                    "source": str(file_path),
                }
            self._vector.add_document_chunks(db_id=shop_db_id, doc_id=doc.id, chunks=chunks)
        except Exception:
            # 向量写入失败：回滚 DB 记录与原件
            self._ks.delete_document(doc.id)
            self._remove_file(stored_path)
            raise
        return True