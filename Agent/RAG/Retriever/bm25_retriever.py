from typing import List, Optional, Tuple
from rank_bm25 import BM25Okapi
from langchain_core.documents import Document
from loguru import logger
import numpy as np


class BM25Retriever:
    """ BM25 关键词检索 """
    def __init__(self, documents: None | List[Document] = None):
        # BM25 索引
        self._bm25: Optional[BM25Okapi] = None
        self._documents: List[Document] = documents
        self._tokenized_corpus: List[List[str]] = []
        if documents:
            self.build_bm25_index(documents)

    def _tokenize(self, text: str) -> List[str]:
        """
        简单的中英文分词

        Args:
            text: 输入文本

        Returns:
            List[str]: 分词结果
        """
        import re
        # 中文按字分，英文按词分
        tokens = []

        # 分割中英文
        segments = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', text.lower())

        for segment in segments:
            # 判断是否是中文
            if re.match(r'[\u4e00-\u9fff]', segment):
                # 中文按字符分割
                tokens.extend(list(segment))
            else:
                # 英文保持原样
                tokens.append(segment)

        return tokens

    def build_bm25_index(self, documents: List[Document]):
        """
        构建 BM25 索引

        Args:
            documents: 文档列表
        """
        self._documents = documents
        self._tokenized_corpus = [
            self._tokenize(doc.page_content)
            for doc in documents
        ]

        self._bm25 = BM25Okapi(self._tokenized_corpus)
        logger.info(f"✅ BM25 索引构建完成: {len(documents)} 个文档")

    def search_with_scores(self, query: str, k: int = 10) -> List[Tuple[Document, float]]:
        """
        BM25 关键词检索

        Args:
            query: 查询文本
            k: 返回结果数量

        Returns:
            List[Tuple[Document, float]]: (文档, 分数) 列表
        """
        if self._bm25 is None:
            logger.warning("⚠️ BM25 索引未构建")
            return []

        tokenized_query = self._tokenize(query)
        scores = self._bm25.get_scores(tokenized_query)

        # 获取 Top-K 索引
        top_indices = np.argsort(scores)[::-1][:k]

        results = [
            (self._documents[i], float(scores[i]))
            for i in top_indices
            if scores[i] > 0
        ]

        return results