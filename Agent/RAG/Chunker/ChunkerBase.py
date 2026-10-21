import json
from pathlib import Path

from typing import List, Optional
import hashlib
from langchain_core.documents import Document
from loguru import logger


class ChunkerBase:
    def __init__(self, chunks_dir: str = "./temp/chunks"):
        self.chunks_dir = Path(chunks_dir)
        # 创建存储目录
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.chunking_method_name = "未知方法"

    def _get_page_content_hash(self, documents: List[Document]):
        # 使用文档内容的哈希值作为缓存标识
        content_hash = hashlib.md5()
        for doc in documents:
            content_hash.update(doc.page_content.encode('utf-8'))

        return content_hash

    def _get_cache_path(self, documents: List[Document]) -> Path:
        """ 钩子 """

    # ===============
    # 执行分块
    # ===============
    def _split_documents(self, documents: List[Document]) -> List[Document]:
        """ 钩子 """

    def split_documents(self, documents: List[Document], use_cache = True) -> List[Document]:
        """
        对文档列表进行 固定分快、语义分块

        Args:
            documents: 原始文档列表
            use_cache: 是否使用缓存

        Returns:
            List[Document]: 分块后的文档列表
        """
        if not documents:
            logger.warning("⚠️ 输入文档列表为空")
            return []

        cache_path = self._get_cache_path(documents)

        # 尝试从缓存加载
        if use_cache:
            cached_chunks = self._load_chunks(cache_path)
            if cached_chunks:
                return cached_chunks

        # 执行分块
        chunks = self._split_documents(documents)

        # 保存到本地
        self._save_chunks(chunks, cache_path)

        return chunks

    # ===============
    # 保存分块
    # ===============

    def _save_single_chunk(self, chunk: Document, index: int, file_path: Path):
        """
        保存单个 chunk 为可读的 Markdown 文件

        Args:
            chunk: 分块文档
            index: 分块索引
            file_path: 文件路径
        """
        source = chunk.metadata.get("file_name", "未知来源")
        method = chunk.metadata.get("chunking_method", "semantic")
        content = f"""# Chunk {index}
        ## 元数据
        - **来源文件**: {source}
        - **字符数**: {len(chunk.page_content)}
        - **分块索引**: {index}
        - **分块方法**: {method}
        
        ## 内容
        
        ```
        {chunk.page_content}
        ```
        """
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def _get_chunks_index(self, total_chunks, data: List[dict]) -> None | dict:
        """ 钩子 """

    def _save_chunks_index(self, chunks_index_dict: None | dict, cache_path: Path):
        # 保存索引文件
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(chunks_index_dict, f, ensure_ascii=False, indent=2)

    def _save_chunks(self, chunks: List[Document], cache_path: Path):
        """
        将分块结果保存到本地文件

        Args:
            chunks: 分块后的文档列表
            cache_path: 缓存文件路径
        """
        data = []
        for i, chunk in enumerate(chunks):
            chunk_data = {
                "index": i,
                "content": chunk.page_content,
                "metadata": chunk.metadata,
                "size": len(chunk.page_content)
            }
            data.append(chunk_data)

            # 同时保存单独的 chunk 文件以便查看
            chunk_file = self.chunks_dir / f"chunk_{i:04d}.md"
            self._save_single_chunk(chunk, i, chunk_file)

        chunks_index_dict = self._get_chunks_index(len(chunks), data)
        self._save_chunks_index(chunks_index_dict, cache_path)
        logger.info(f"💾 分块数据已保存: {cache_path}")

    # ===============
    # 加载分块
    # ===============

    def _load_chunks(self, cache_path: Path) -> Optional[List[Document]]:
        """
        从本地文件读取分块结果

        Args:
            cache_path: 缓存文件路径

        Returns:
            Optional[List[Document]]: 分块文档列表，如果不存在则返回 None
        """
        if not cache_path.exists():
            return None

        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            chunks = []
            for chunk_data in data["chunks"]:
                doc = Document(
                    page_content=chunk_data["content"],
                    metadata=chunk_data["metadata"]
                )
                chunks.append(doc)

            logger.info(f"📂 从缓存加载{self.chunking_method_name}分块: {len(chunks)} 个块")
            return chunks
        except Exception as e:
            logger.warning(f"⚠️ 加载缓存失败: {e}")
            return None

    def clear_cache(self):
        """清空所有缓存的分块文件"""
        import shutil
        if self.chunks_dir.exists():
            shutil.rmtree(self.chunks_dir)
            self.chunks_dir.mkdir(parents=True, exist_ok=True)
            logger.info("🗑️ 分块缓存已清空")

    def list_cached_chunks(self) -> List[Path]:
        """
        列出所有缓存的 chunk 文件

        Returns:
            List[Path]: chunk 文件路径列表
        """
        if not self.chunks_dir.exists():
            return []

        return sorted(self.chunks_dir.glob("chunk_*.md"))

    def _print_chunks_info(self, chunks: List[Document]):
        # 打印分块统计信息
        sizes = [len(c.page_content) for c in chunks]
        avg_size = sum(sizes) / len(sizes) if sizes else 0
        logger.info(
            f"📊 分块统计: 平均大小={avg_size:.0f}, "
            f"最小={min(sizes) if sizes else 0}, "
            f"最大={max(sizes) if sizes else 0}"
        )


