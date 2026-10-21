from typing import List
from langchain_core.documents import Document
from loguru import logger


class RetrieverBase:
    def __init__(self):
        pass

    def search(self, query: str, k: int = 5) -> List[Document]:
        """
        检索

        Args:
            query: 查询文本
            k: 返回结果数量

        Returns:
            List[Document]: 检索结果
        """





