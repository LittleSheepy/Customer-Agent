from typing import List
from langchain_core.documents import Document
from loguru import logger


class VectorStoreBase:
    def __init__(self, collection_name, doc_id, file_path):
        self.collection_name = collection_name
        pass

    # ==============
    # 添加文档
    # ==============
    def add_documents(self, documents: List[Document]) -> None | List[str]:
        if not documents:
            logger.warning("⚠️ 没有文档需要添加")
            return []


    # ==============
    # 相似度检索
    # ==============
    # def similarity_search(
    #     self,
    #     query: str,
    #     k: Optional[int] = None
    # ) -> List[Document]: