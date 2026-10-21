"""
Agent 包 — 统一导出层

外部模块（ui / core / database 等）应只从 Agent 包导入，
不要直接引用 Agent 内部子模块路径，以免内部结构调整时波及外部。
"""

# 共享基础设施
from Agent.session_manager import SessionManager
from Agent.tool_decorator import agent_tool, TOOL_REGISTRY, get_tools_for_llm, get_tools_by_names, get_tool_entry, execute_tool, ToolEntry
from Agent.llm_client import create_chat_model

# 韧性层
from Agent.resilience import ResilientModel, CircuitBreaker, CircuitOpenError

# 中间件
from Agent.middleware import MiddlewarePipeline

# 可观测性（从 utils 导出，方便其他模块直接使用）
from utils.observability import metrics, RequestContext, request_scope, get_request_id

# RAG 服务
from Agent.RAG.document_service import DocumentIndexService
from Agent.RAG.vector_store import VectorSearchService
from Agent.RAG.self_rag import SelfRAGPipeline, get_shared_pipeline

# 触发所有 @agent_tool 装饰器注册到 TOOL_REGISTRY
import Agent.tools  # noqa: F401

__all__ = [
    # 共享基础设施
    "SessionManager",
    "agent_tool",
    "TOOL_REGISTRY",
    "get_tools_for_llm",
    "get_tools_by_names",
    "get_tool_entry",
    "execute_tool",
    "ToolEntry",
    "create_chat_model",
    # 韧性层
    "ResilientModel",
    "CircuitBreaker",
    "CircuitOpenError",
    # 中间件
    "MiddlewarePipeline",
    # 可观测性
    "metrics",
    "RequestContext",
    "request_scope",
    "get_request_id",
    # RAG 服务
    "DocumentIndexService",
    "VectorSearchService",
    "SelfRAGPipeline",
    "get_shared_pipeline",
]
