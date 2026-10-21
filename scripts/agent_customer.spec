# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Agent-Customer
生成命令: pyinstaller scripts/agent_customer.spec
"""

import sys
import os
from pathlib import Path
import tomllib

block_cipher = None

# 项目根目录（spec 文件位于 scripts/ 目录，上两级为项目根目录）
if '__file__' in globals():
    _spec_dir = Path(os.path.abspath(__file__)).parent
    PROJECT_ROOT = _spec_dir.parent
elif len(sys.argv) > 0:
    # fallback: 从命令行参数推导
    PROJECT_ROOT = Path(sys.argv[0]).resolve().parent.parent
else:
    # last fallback: 从 cwd 推导
    PROJECT_ROOT = Path.cwd()

try:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as _version_file:
        PROJECT_VERSION = tomllib.load(_version_file)["project"]["version"]
except (OSError, KeyError, TypeError):
    PROJECT_VERSION = "0.0.0-dev"

# 虚拟环境路径（用于引用 site-packages 中的文件）
venv_path = PROJECT_ROOT / ".venv"

# ================================
# Playwright 驱动打包
# ================================
# collect_all 收集 playwright/driver/ 下的 node 与 JS，否则打包后启动浏览器会
# 因驱动缺失而失败。配合 pdd_login 的 channel="chrome"，用户无需安装 Playwright 浏览器。
from PyInstaller.utils.hooks import collect_all, collect_submodules
_pw_datas, _pw_binaries, _pw_hidden = collect_all("playwright")
_llm_hidden = [
    "litellm",
    "litellm.main",
    "litellm.llms",
]
for _provider_package in (
    "litellm.llms.openai",
    "litellm.llms.deepseek",
    "litellm.llms.volcengine",
    "litellm.llms.moonshot",
    "litellm.llms.zai",
    "litellm.llms.openai_like",
    "litellm.llms.dashscope",
    "litellm.llms.custom_httpx",
):
    _llm_hidden.extend(collect_submodules(_provider_package))

# node.exe 不会进入 binaries，但会出现在 datas（目标 playwright/driver），这里显式补进 binaries
# 以便在 EXE 元信息（VirusTotal/安全软件）层面可见，行为与 datas 等价，属防御性冗余。
_pw_node = [
    (src, dst) for src, dst in _pw_datas if src.lower().endswith("node.exe")
]
_pw_binaries = list(_pw_binaries) + _pw_node

# ================================
# 基础配置
# ================================
a = Analysis(
    [str(PROJECT_ROOT / "app.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=_pw_binaries,
    datas=[
        # 图标文件
        (str(PROJECT_ROOT / "icon" / "icon.ico"), "icon"),
        (str(PROJECT_ROOT / "icon" / "Customer-Agent-qr.png"), "icon"),
        # chromadb 用 pkgutil.iter_modules 动态发现嵌入函数类，
        # 冻结环境下 pkgutil 只能扫描磁盘文件，PYZ 归档内的模块不可见，
        # 因此必须把 ONNX 嵌入函数源文件提取到磁盘。
        (
            str(venv_path / "Lib" / "site-packages" / "chromadb" / "utils" / "embedding_functions" / "onnx_mini_lm_l6_v2.py"),
            str(Path("chromadb") / "utils" / "embedding_functions"),
        ),
    ] + _pw_datas,
    hiddenimports=[
        # === PyQt6 & Fluent Widgets ===
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.sip",
        "qfluentwidgets",
        "qfluentwidgets.common",
        "qfluentwidgets.components",
        "qfluentwidgets.window",
        # === AI / LLM 核心 ===
        "openai",
        "openai._models",
        "openai._client",
        "litellm",
        "litellm.main",
        "litellm.litellm_core_utils",
        "litellm.llms.openai",
        "litellm.llms.deepseek",
        "litellm.llms.volcengine",
        "litellm.llms.moonshot",
        "litellm.llms.zai",
        "tiktoken",
        "tiktoken_ext",
        "tiktoken_ext.openai_public",
        # === LangChain 生态 ===
        "langchain_core",
        "langchain_core.documents",
        "langchain_core.prompts",
        "langchain_core.embeddings",
        "langchain_openai",
        "langchain_chroma",
        "langchain_community",
        "langchain_community.document_loaders",
        "langchain_community.document_compressors",
        "langchain_community.document_compressors.dashscope_rerank",
        "langchain_text_splitters",
        "chromadb",
        "chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2",
        "tenacity",
        # === 嵌入 & 检索 ===
        "dashscope",
        "jieba",
        "rank_bm25",
        # === 文档解析 ===
        "pypdf",
        "docx",
        "docx.document",
        "docx.oxml",
        "docx2txt",
        "unstructured",
        "openpyxl",
        "openpyxl.cell",
        "openpyxl.workbook",
        # === 数据库 ===
        "sqlalchemy",
        "sqlalchemy.orm",
        "sqlalchemy.sql",
        "sqlalchemy.dialects.sqlite",
        # === 日志 ===
        "loguru",
        "loguru._logger",
        # === Web / 网络 ===
        "websockets",
        "websockets.client",
        "websockets.server",
        "aiohttp",
        "aiohttp.client",
        "aiohttp.web",
        "requests",
        "urllib3",
        "charset_normalizer",
        "httpx",
        "httpcore",
        "certifi",
        "dotenv",
        # === 浏览器自动化 ===
        "playwright",
        "playwright._impl",
        "playwright.async_api",
        # === 数据处理 ===
        "pandas",
        "pandas._libs",
        "numpy",
        "numpy.core",
        "numpy._core",
        # === 图像 ===
        "PIL",
        "PIL._imaging",
        # === 异步 ===
        "asyncio",
        "aiofiles",
        # === 工具类 ===
        "pydantic",
        "pydantic.fields",
        "pydantic.dataclasses",
        "pydantic.v1",
        "pydantic.v1.fields",
        "volcengine",
        "volcengine.base",
        "volcengine.viking_knowledgebase",
        "volcengine.viking_knowledgebase.Collection",
        "volcengine.viking_knowledgebase.Doc",
        "rich",
        # === importlib ===
        "importlib",
        "importlib.util",
        "importlib.abc",
        # === Agent 核心基础设施 ===
        "config",
        "Agent.AgentBase",
        "Agent.factory",
        "Agent.llm_client",
        "Agent.message_builder",
        "Agent.middleware",
        "Agent.prompts_utils",
        "Agent.resilience",
        "Agent.session_manager",
        "Agent.tool_decorator",
        "Agent.utils",
        # === Agent 工具 ===
        "Agent.tools",
        "Agent.tools.get_product_knowledge",
        "Agent.tools.get_product_list",
        "Agent.tools.move_conversation",
        "Agent.tools.search_customer_service_knowledge",
        "Agent.tools.self_rag_search",
        "Agent.tools.send_goods_link",
        # === AgenticRAGAgent ===
        "Agent.AgenticRAGAgent",
        "Agent.AgenticRAGAgent.agentic_rag_agent",
        "Agent.AgenticRAGAgent.prompts",
        "Agent.AgenticRAGAgent.router",
        "Agent.AgenticRAGAgent.schemas",
        # === MultiAgent 多智能体 ===
        "Agent.MultiAgent",
        "Agent.MultiAgent.supervisor",
        "Agent.MultiAgent.router",
        "Agent.MultiAgent.prompts",
        "Agent.MultiAgent.schemas",
        "Agent.MultiAgent.SubAgent",
        "Agent.MultiAgent.SubAgent.base",
        "Agent.MultiAgent.SubAgent.product_agent",
        "Agent.MultiAgent.SubAgent.after_sales_agent",
        "Agent.MultiAgent.SubAgent.chat_agent",
        # === RAG 管线 ===
        "Agent.RAG.document_loader",
        "Agent.RAG.document_service",
        "Agent.RAG.embedder",
        "Agent.RAG.self_rag",
        "Agent.RAG.vector_store",
        "Agent.RAG.Chunker.ChunkerBase",
        "Agent.RAG.Chunker.chunker",
        "Agent.RAG.Chunker.semantic_chunker",
        "Agent.RAG.Reranker.reranker",
        "Agent.RAG.Retriever.RetrieverBase",
        "Agent.RAG.Retriever.bm25_retriever",
        "Agent.RAG.Retriever.hybrid_retriever",
        "Agent.RAG.Retriever.vector_retriever",
        "Agent.RAG.VectorStore.VectorStoreBase",
        "Agent.RAG.VectorStore.vector_store_chroma",
        # === 核心 DI / 服务 ===
        "core.di_container",
        "core.connection_status",
        "core.base_service",
        "core.service_providers",
        # === 数据库 ===
        "database.db_manager",
        "database.models",
        "database.knowledge_service",
        "database.product_sync",
        # === Bridge ===
        "bridge.context",
        "bridge.reply",
        "bridge.sender",
        # === Message 消息系统 ===
        "Message.message",
        "Message.core.queue",
        "Message.core.consumer",
        "Message.core.handlers",
        "Message.models.queue_models",
        "Message.handlers.base",
        "Message.handlers.ai_handler",
        "Message.handlers.keyword_handler",
        "Message.handlers.preprocessor",
        # === Channel 拼多多 ===
        "Channel.channel",
        "Channel.pinduoduo.pdd_channel",
        "Channel.pinduoduo.pdd_message",
        "Channel.pinduoduo.pdd_login",
        "Channel.pinduoduo.cookie_cache",
        "Channel.pinduoduo.cookie_utils",
        "Channel.pinduoduo.core",
        "Channel.pinduoduo.core.pdd_config",
        "Channel.pinduoduo.core.pdd_connection",
        "Channel.pinduoduo.core.pdd_lifecycle",
        "Channel.pinduoduo.core.pdd_message_handler",
        "Channel.pinduoduo.core.pdd_status",
        "Channel.pinduoduo.core.pdd_utils",
        "Channel.pinduoduo.utils.base_request",
        "Channel.pinduoduo.utils.API.get_token",
        "Channel.pinduoduo.utils.API.send_message",
        "Channel.pinduoduo.utils.API.get_shop_info",
        "Channel.pinduoduo.utils.API.Set_up_online",
        "Channel.pinduoduo.utils.API.get_user_info",
        "Channel.pinduoduo.utils.API.get_messages",
        "Channel.pinduoduo.utils.API.get_messages_users",
        "Channel.pinduoduo.utils.API.get_custom_service_info",
        "Channel.pinduoduo.utils.API.product_manager",
        # === Service ===
        "service.account_service",
        "service.keyword_service",
        # === UI ===
        "ui.main_ui",
        "ui.auto_reply_ui",
        "ui.auto_reply",
        "ui.auto_reply.card",
        "ui.auto_reply.chat_panel",
        "ui.auto_reply.manager",
        "ui.auto_reply.threads",
        "ui.auto_reply.ui",
        "ui.Knowledge_ui",
        "ui.Knowledge_ui.knowledge_ui",
        "ui.Knowledge_ui.dialogs",
        "ui.Knowledge_ui.workers",
        "ui.keyword_ui",
        "ui.log_ui",
        "ui.setting_ui",
        "ui.user_ui",
        # === Utils 工具 ===
        "utils.async_helper",
        "utils.db_pragma",
        "utils.encoding_helper",
        "utils.file_validator",
        "utils.llm_provider",
        "utils.logger_loguru",
        "utils.logger_config",
        "utils.logging_context",
        "utils.observability",
        "utils.onnx_stub",
        "utils.path_utils",
        "utils.resource_manager",
        "utils.runtime_path",
        "utils.safe_image_fetch",
        "utils.secret_store",
    ] + _pw_hidden + _llm_hidden,
    hookspath=[],
    hooksconfig={},
    keys=block_cipher,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # Windows 下关闭 UPX（兼容性更好）
    console=False,        # 不显示控制台窗口（GUI 程序）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ================================
# 生成 PYZ（Python 库）
# ================================
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ================================
# 生成 EXE
# ================================
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AgentCustomer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "icon" / "icon.ico"),
    # Windows 特定
    version=str(PROJECT_ROOT / "scripts" / "version_info.txt"),
    description="电商AI客服助手",
    product_name="Agent-Customer",
    product_version=PROJECT_VERSION,
    company_name="",
    legal_copyright="",
    # 权限
    RequestedExecutionLevel="asInvoker",
)

# ================================
# 收集所有文件到 dist 目录
# ================================
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AgentCustomer",
)
