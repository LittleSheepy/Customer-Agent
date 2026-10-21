"""
Agent/AgenticRAGAgent 结构化输出模型
====================================

参照 UltimateRAG stage_3/self_rag.py 的评估模型部分。

路由/相关性/质量三处评估均通过 with_structured_output 使用，
失败时由调用方用默认值兜底。
"""
from enum import Enum

from pydantic import BaseModel, Field


# ==============================================================================
# 路由
# ==============================================================================

class RouteType(str, Enum):
    """路由类型（仅保留本项目可用的两个分支）"""
    KNOWLEDGE_BASE = "knowledge_base"  # 知识库检索（自反思 RAG）
    DIRECT_ANSWER = "direct_answer"    # 直接回答（问候/闲聊/常识）


class RouteDecision(BaseModel):
    """路由决策结果"""
    route_type: RouteType = Field(description="选择的路由类型")
    confidence: float = Field(description="决策置信度，0-1 之间", ge=0.0, le=1.0)
    reasoning: str = Field(description="选择该路由的原因")
    transformed_query: str = Field(description="针对该路由优化后的查询")


# ==============================================================================
# 相关性评估
# ==============================================================================

class RelevanceGrade(str, Enum):
    """检索相关性等级"""
    HIGHLY_RELEVANT = "highly_relevant"        # 高度相关
    PARTIALLY_RELEVANT = "partially_relevant"  # 部分相关
    NOT_RELEVANT = "not_relevant"              # 不相关


class RelevanceEvaluation(BaseModel):
    """检索相关性评估结果"""
    grade: RelevanceGrade = Field(description="相关性等级")
    score: float = Field(description="相关性分数 0-1", ge=0.0, le=1.0)
    reasoning: str = Field(description="评估原因")


# ==============================================================================
# 质量评估
# ==============================================================================

class QualityGrade(str, Enum):
    """答案质量等级"""
    EXCELLENT = "excellent"                    # 优秀
    ACCEPTABLE = "acceptable"                  # 可接受
    NEEDS_IMPROVEMENT = "needs_improvement"    # 需要改进
    INSUFFICIENT = "insufficient"              # 不足


class QualityEvaluation(BaseModel):
    """答案质量评估结果"""
    grade: QualityGrade = Field(description="质量等级")
    score: float = Field(description="质量分数 0-1", ge=0.0, le=1.0)
    is_complete: bool = Field(description="答案是否完整")
    is_accurate: bool = Field(description="答案是否准确")
    missing_info: str = Field(description="缺失的信息描述")
    suggestions: str = Field(description="改进建议")
