"""
文档加载模块

支持加载多种格式的文档：
- PDF 文件
- Markdown 文件
- 纯文本文件
- DOCX 文件
"""

import os
from pathlib import Path
from typing import List, Union
from loguru import logger
from langchain_core.documents import Document
from langchain_community.document_loaders import (
    PyPDFLoader,
    UnstructuredMarkdownLoader,
    TextLoader,
    Docx2txtLoader,
    DirectoryLoader,
)


class DocumentLoader:
    """
    文档加载器
    
    支持加载单个文件或整个目录中的文档。
    自动根据文件扩展名选择合适的加载器。
    """
    
    # 支持的文件扩展名与加载器映射
    LOADER_MAPPING = {
        ".pdf": PyPDFLoader,
        # ".md": UnstructuredMarkdownLoader,
        ".md": TextLoader,
        ".txt": TextLoader,
        ".docx": Docx2txtLoader,
    }
    
    def __init__(self):
        """初始化文档加载器"""
        logger.info("📄 文档加载器初始化完成")

    def load(self, path: Union[str, Path]) -> List[Document]:
        """
        智能加载：自动判断是文件还是目录

        Args:
            path: 文件或目录路径

        Returns:
            List[Document]: 文档列表
        """
        path = Path(path)

        if path.is_file():
            return self._load_file(path)
        elif path.is_dir():
            return self._load_directory(path)
        else:
            raise FileNotFoundError(f"路径不存在: {path}")

    def _load_file(self, file_path: Union[str, Path]) -> List[Document]:
        """
        加载单个文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            List[Document]: 文档列表
            
        Raises:
            ValueError: 不支持的文件格式
            FileNotFoundError: 文件不存在
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        suffix = file_path.suffix.lower()
        if suffix not in self.LOADER_MAPPING:
            raise ValueError(
                f"不支持的文件格式: {suffix}。"
                f"支持的格式: {list(self.LOADER_MAPPING.keys())}"
            )
        
        loader_class = self.LOADER_MAPPING[suffix]
        
        try:
            loader = loader_class(str(file_path), encoding="utf-8-sig")
            documents = loader.load()
            logger.info(f"✅ 已加载文件: {file_path.name}，共 {len(documents)} 个文档片段")
            
            # 添加元数据
            for doc in documents:
                doc.metadata["source_file"] = str(file_path)
                doc.metadata["file_name"] = file_path.name
                doc.metadata["file_type"] = suffix
                
            return documents
        except Exception as e:
            logger.error(f"❌ 加载文件失败: {file_path.name}，错误: {e}")
            raise
    
    def _load_directory(self, directory_path: Union[str, Path], recursive: bool = True) -> List[Document]:
        """
        加载目录中的所有支持的文件
        
        Args:
            directory_path: 目录路径
            recursive: 是否递归加载子目录
            
        Returns:
            List[Document]: 所有文档列表
        """
        directory_path = Path(directory_path)
        
        if not directory_path.exists():
            raise FileNotFoundError(f"目录不存在: {directory_path}")
        
        if not directory_path.is_dir():
            raise ValueError(f"路径不是目录: {directory_path}")
        
        all_documents = []
        pattern = "**/*" if recursive else "*"
        
        for suffix in self.LOADER_MAPPING.keys():
            files = list(directory_path.glob(f"{pattern}{suffix}"))
            for file_path in files:
                try:
                    documents = self._load_file(file_path)
                    all_documents.extend(documents)
                except Exception as e:
                    logger.warning(f"⚠️ 跳过文件 {file_path.name}: {e}")
        
        logger.info(f"📁 目录加载完成: 共 {len(all_documents)} 个文档片段")
        return all_documents
