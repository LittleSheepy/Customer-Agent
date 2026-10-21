"""
chromadb 默认嵌入函数规避
========================

chromadb 0.6.x 在 CollectionCommon 类定义时调用 DefaultEmbeddingFunction()，
该函数实例化 ONNXMiniLM_L6_V2 并尝试加载 onnxruntime。

本项目使用远程 DashScope Embedding，不需要本地 ONNX 模型；
且 onnxruntime 原生库会与项目预加载的 core 包产生 DLL 冲突。

本模块通过 sys.meta_path 导入钩子，在 CollectionCommon 加载前
将 DefaultEmbeddingFunction 桩为返回 None，
使 chromadb 导入链完整走通，同时彻底避免 onnxruntime 依赖。

用法：在所有 import chromadb / langchain_chroma 的文件顶部，
首先 ``import utils.onnx_stub  # noqa: F401``。
"""

import sys as _sys


def _install_hook():
    """安装一次性导入钩子：拦截 CollectionCommon 加载，桩掉 DefaultEmbeddingFunction"""
    if "chromadb" in _sys.modules:
        return  # chromadb 已加载，无需钩子

    class _DefaultEFPatchFinder:
        """在 CollectionCommon 加载前桩掉 DefaultEmbeddingFunction 的导入钩子"""

        _patched = False

        def find_spec(self, fullname, path, target=None):
            if fullname != "chromadb.api.models.CollectionCommon" or self._patched:
                return None
            try:
                ef = _sys.modules.get("chromadb.utils.embedding_functions")
                if ef is not None:
                    ef.DefaultEmbeddingFunction = lambda: None
                    self._patched = True
            except Exception:
                pass
            return None  # 不干预正常加载

    _sys.meta_path.insert(0, _DefaultEFPatchFinder())


_install_hook()
