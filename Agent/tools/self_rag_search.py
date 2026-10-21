"""
SelfRAG 深度知识检索工具
========================

执行 SelfRAG 精检索管线（只做检索+评估，不生成答案）：
  混合检索 → 重排序 → 相关性评估过滤 → 迭代优化
  → 返回经质量验证的精选文档

供 MultiAgent 子 Agent 在 Agent Loop 中按需调用，
补充 Phase 1（_get_rag_context）自动检索可能遗漏的信息。
"""
from typing import Optional, Union
from pydantic import BaseModel, Field

from Agent.tool_decorator import agent_tool
from Agent.utils import format_documents
from database.db_manager import get_db_manager
from utils.logger_loguru import get_logger

logger = get_logger("SelfRagSearchTool")


def _get_pipeline(shop_db_id: int):
    """获取指定店铺的 SelfRAGPipeline 实例"""
    from Agent.RAG.self_rag import get_shared_pipeline
    return get_shared_pipeline(shop_db_id)


class SelfRagSearchParams(BaseModel):
    """SelfRAG 深度检索参数"""
    query: str = Field(..., description="检索查询语句")
    shop_id: Optional[Union[str, int]] = Field(default=None, description="店铺ID")


@agent_tool(
    name="self_rag_search",
    description="深度知识检索。当初始提供的参考信息不足以回答问题时，"
                "用此工具进行更精准的检索。返回经过相关性评估的精选文档（不含答案生成）。",
    param_model=SelfRagSearchParams,
)
def self_rag_search(params: SelfRagSearchParams) -> str:
    """执行 SelfRAG 精检索管线，返回精选文档"""
    try:
        if not params.shop_id:
            return "[深度检索失败：缺少店铺ID]"

        # 平台 shop_id（来自 LLM 工具调用）
        shop_id = int(params.shop_id)

        # 转换为数据库 shop_db_id（用于 Pipeline 和向量检索）
        shop_db_id = get_db_manager().resolve_shop_db_id(shop_id)
        if not shop_db_id:
            return f"[深度检索失败：无法解析店铺数据库ID shop_id={shop_id}]"

        pipeline = _get_pipeline(shop_db_id)

        docs = pipeline.retrieve_refined(
            shop_db_id=shop_db_id,
            query=params.query,
        )

        if not docs:
            return "[深度检索未找到相关文档]"

        formatted = format_documents(docs[:5])
        logger.info(f"self_rag_search: query={params.query[:30]}, 返回 {len(docs)} 篇文档")
        return f"以下是深度检索找到的参考文档：\n{formatted}"

    except Exception as e:
        logger.error(f"self_rag_search 失败: {type(e).__name__}:{e}")
        return "[深度检索失败，请稍后重试]"
