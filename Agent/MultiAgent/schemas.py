"""
Agent/MultiAgent 结构化输出模型
================================

多智能体架构的意图分类与调度决策模型。
均通过 with_structured_output 使用，
失败时由调用方用默认值兜底。
"""
from enum import Enum

from pydantic import BaseModel, Field


# ==============================================================================
# 意图分类
# ==============================================================================

class IntentType(str, Enum):
    """客服场景意图类型"""
    PRODUCT_INQUIRY = "product_inquiry"      # 商品咨询（成分、规格、价格、用法等）
    AFTER_SALES = "after_sales"              # 售后服务（退换货、维修、投诉、物流等）
    CHAT = "chat"                            # 闲聊/问候/通用对话


class IntentClassification(BaseModel):
    """意图分类结果"""
    intent: IntentType = Field(description="识别的意图类型")
    confidence: float = Field(description="分类置信度，0-1 之间", ge=0.0, le=1.0)
    reasoning: str = Field(description="分类原因")
    extracted_entities: str = Field(
        default="",
        description="提取的关键实体，如商品名称、订单号等（无则留空）",
    )


class SupervisorDecision(BaseModel):
    """Supervisor 调度决策"""
    target_agent: str = Field(description="目标子 Agent 名称: product / after_sales / chat")
    intent: IntentType = Field(description="识别的意图类型")
    confidence: float = Field(description="决策置信度", ge=0.0, le=1.0)
    reasoning: str = Field(description="调度原因")
