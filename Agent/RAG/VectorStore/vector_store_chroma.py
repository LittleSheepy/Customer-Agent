from typing import List, Tuple, Optional, Dict
from langchain_core.documents import Document
from loguru import logger

import utils.onnx_stub  # noqa: F401  桩掉 chromadb 默认 ONNX 嵌入函数
import chromadb
from langchain_chroma import Chroma

from Agent.RAG.embedder import EmbeddingModel
from config import get_config


class VectorStoreChroma:
    """Chroma 向量数据库"""
    def __init__(self, collection_name):
        self.collection_name = collection_name
        self._persist_dir = get_config("embedding.persist_dir")
        # 初始化嵌入模型
        self._embedding_model = EmbeddingModel()
        from langchain_chroma import Chroma
        self._vectorstore : Chroma = Chroma(
            collection_name=self.collection_name,
            embedding_function=self._embedding_model.embeddings,
            persist_directory=str(self._persist_dir),
        )
        self._build_store : None | Chroma= None
        logger.info(f"VectorStoreChroma {collection_name} 初始化成功: persist_dir={self._persist_dir}")

    # ==============
    # 相似度检索
    # ==============
    def similarity_search(self, query: str, k: int = 5, filter: Optional[Dict[str, str]] = None) -> List[Document]:
        return self._vectorstore.similarity_search(query, k=k, filter=filter)

    def similarity_search_with_score(self, query: str, k: int = 5) -> List[Tuple[Document, float]]:
        return self._vectorstore.similarity_search_with_score(query, k=k)

    # ==============
    # 添加文档
    # ==============
    def _add_in_batches(self, docs: List[Document], store = None) -> None:
        """按 embedding.batch_size 分片写入（DashScope 单请求上限 10 条）"""
        store = store or self._vectorstore
        total = len(docs)
        batch_size = get_config("embedding.batch_size", 10)
        batches = (total + batch_size - 1) // batch_size
        for index in range(0, total, batch_size):
            batch = docs[index:index + batch_size]
            store.add_documents(batch)
            logger.debug(f"向量写入分片 {index // batch_size + 1}/{batches} 完成（本批 {len(batch)} 条）")

    # ==============
    # 获取文档
    # ==============
    def get_all_chunks(self) -> List[Dict]:
        """获取集合中所有 chunks（使用 Chroma 高层 API，不直接操作 _collection）"""
        data = self._vectorstore.get()  # 不传参数，返回全部

        results = []
        for i in range(len(data["ids"])):
            chunk = {
                "id": data["ids"][i],
                "document": data["documents"][i] if data["documents"] else None,
                "metadata": data["metadatas"][i] if data["metadatas"] else {},
            }
            results.append(chunk)

        logger.info(f"获取所有 chunks 完成: 共 {len(results)} 条")
        return results

    def get_all_documents(self) -> List[Document]:
        """获取集合中所有文档"""
        data = self._vectorstore.get()  # 不传参数，返回全部
        documents = []
        for i, text in enumerate(data.get("documents", [])):
            metadata = data.get("metadatas", [{}])[i] if data.get("metadatas") else {}
            doc = Document(
                page_content=text,
                metadata=metadata,
            )
            documents.append(doc)
        return documents

    def set_document_enabled(self, doc_id: int, enabled: bool) -> None:
        """启停文档：只更新 chunk 元数据，不重新 embedding"""
        # 通过 _collection 访问底层原生 API，按条件查出所有相关 chunk
        collection = self._vectorstore._collection
        data = collection.get(where={"doc_id": str(doc_id)})
        ids = data.get("ids", [])
        if not ids:
            return

        # 合并式更新 enabled 字段（保留其他元数据）
        flag = "true" if enabled else "false"
        metadatas = [
            {**(meta or {}), "enabled": flag}
            for meta in data.get("metadatas", [])
        ]
        collection.update(ids=ids, metadatas=metadatas)

    def delete_document_chunks1(self, db_id: int, doc_id: int) -> None:
        """删除文档全部 chunk 向量

        注意：Chroma 0.6.x 的 collection.delete(where=...) 存在静默失败问题，
        需先 get(where=...) 拿到 ID 列表，再按 ID 删除。
        """
        collection = self._vectorstore._collection
        data = collection.get(where={"doc_id": str(doc_id)})
        ids = data.get("ids", [])
        if not ids:
            logger.debug(f"未找到待删除向量: db_id={db_id}, doc_id={doc_id}")
            return
        collection.delete(ids=ids)

    def delete_document_chunks(self, doc_id: int) -> None:
        """删除文档全部 chunk 向量"""
        # self._vectorstore.delete(filter={"doc_id": str(doc_id)})
        # ✅ 按元数据条件删除（通过底层 API）
        self._vectorstore._collection.delete(where={"doc_id": str(doc_id)})


    def delete_collection(self):
        self._vectorstore.delete_collection()

    def reset_collection(self):
        self._vectorstore.reset_collection()

    def rebuild_collection(self, docs: List[Document]):
        self.reset_collection()
        self._add_in_batches(docs)

    def rebuild_collection1(self, docs: List[Document]) -> Optional[Chroma]:
        build_name = self.collection_name + "_build"
        if not docs:
            return None
        # 清理上次可能残留的临时集合
        try:
            if self._vectorstore:
                # self._build_store.delete()
                self._vectorstore.reset_collection()
        except Exception:
            pass
        #
        # self._build_store = Chroma(
        #     collection_name=build_name,
        #     embedding_function=self._embedding_model.embeddings,
        #     persist_directory=str(self._persist_dir),
        # )

        try:
            self._add_in_batches(docs, self._vectorstore)
        except Exception:
            try:
                self._vectorstore.reset_collection()
            except Exception:
                pass
            raise


        # # 全部写入成功，替换正式集合
        # try:
        #     self._vectorstore.delete_collection()
        # except Exception:
        #     pass
        # self._client.get_collection(build_name).modify(name=final_name)
        # logger.info(
        #     f"{kind}向量索引重建完成: db_shop_id={db_id}, docs={len(docs)}"
        # )

        return None

if __name__ == '__main__':
    vsc = VectorStoreChroma("shop_2_docs")
    chunks = vsc.get_all_chunks()
    documents = vsc.get_all_documents()
    pass

