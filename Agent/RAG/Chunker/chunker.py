"""
文本分块模块

实现 FixedSizeChunking（固定大小分块）策略。
Phase 1 使用基础的固定分块，后续阶段将引入语义分块。

优化功能：
- 支持将分块结果保存到本地文件
- 支持从本地文件读取已保存的分块
"""

import json
import hashlib
from pathlib import Path
from typing import List, Optional
from loguru import logger

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from Agent.RAG.Chunker.ChunkerBase import ChunkerBase


class TextChunker(ChunkerBase):
    """
    文本分块器
    
    将长文档切分为固定大小的小块，便于向量化和检索。
    使用 RecursiveCharacterTextSplitter 实现智能分割。
    
    支持将分块结果保存到本地，下次可直接读取。
    """
    
    def __init__(self, chunk_size: int = 256, chunk_overlap: int = 50, separators: List[str] = None,
                 chunks_dir: str = "./temp/chunks"):
        """
        初始化文本分块器
        
        Args:
            chunk_size: 每个块的最大字符数
            chunk_overlap: 块之间的重叠字符数
            separators: 分割符列表，按优先级排序
            chunks_dir: 分块数据存储目录
        """
        super().__init__(chunks_dir)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        
        # 默认分割符：按段落 -> 句子 -> 词语顺序分割
        self.separators = separators or [
            "\n\n",  # 段落
            "\n",    # 换行
            "。",    # 中文句号
            ".",     # 英文句号
            "！",    # 中文感叹号
            "!",     # 英文感叹号
            "？",    # 中文问号
            "?",     # 英文问号
            "；",    # 中文分号
            ";",     # 英文分号
            "，",    # 中文逗号
            ",",     # 英文逗号
            " ",     # 空格
            "",      # 字符级别
        ]
        
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=self.separators,
            length_function=len,
            is_separator_regex=False,
        )
        
        logger.info(f"✂️ 文本分块器初始化:  chunk_size={chunk_size}, overlap={chunk_overlap},  存储目录={self.chunks_dir}")
    
    def _get_cache_path(self, documents: List[Document]) -> Path:
        """
        根据文档内容生成缓存文件路径
        
        Args:
            documents: 文档列表
            
        Returns:
            Path: 缓存文件路径
        """
        # 使用文档内容的哈希值作为缓存标识
        content_hash = self._get_page_content_hash(documents)
        
        # 加入分块参数作为哈希的一部分
        params = f"{self.chunk_size}_{self.chunk_overlap}"
        content_hash.update(params.encode('utf-8'))
        
        return self.chunks_dir / f"chunks_fixed_{content_hash.hexdigest()[:16]}.json"

    # ===============
    # 保存分块
    # ===============
    def _get_chunks_index(self, total_chunks, data: List[dict]) -> None | dict:
        return {
            "total_chunks": total_chunks,
            "chunking_method": "fixed",
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "chunks": data
        }


    def _split_documents(self, documents: List[Document]) -> List[Document]:
        logger.info("🔄 开始分块处理...")
        chunks = self.splitter.split_documents(documents)

        # 为每个块添加索引信息
        for i, chunk in enumerate(chunks):
            chunk.metadata["chunk_index"] = i
            chunk.metadata["chunk_size"] = len(chunk.page_content)
            chunk.metadata["chunking_method"] = "fixed_size"

        logger.info(f"✅ 分块完成: {len(documents)} 个文档 -> {len(chunks)} 个块")

        # 打印分块统计信息
        self._print_chunks_info(chunks)
        return chunks

    def split_text(self, text: str) -> List[str]:
        """
        对纯文本进行分块
        
        Args:
            text: 原始文本
            
        Returns:
            List[str]: 分块后的文本列表
        """
        if not text:
            return []
        
        chunks = self.splitter.split_text(text)
        logger.info(f"✅ 文本分块完成: {len(text)} 字符 -> {len(chunks)} 个块")
        return chunks
