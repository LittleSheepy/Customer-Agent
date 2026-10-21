"""
Agent/MultiAgent - 多智能体调度架构

SupervisorAgent  →  QueryClassifier(意图分类)  →  子 Agent 调度
    ├── ProductAgent     (商品咨询)
    ├── AfterSalesAgent  (售后服务)
    └── ChatAgent        (闲聊/问候)
"""

from Agent.MultiAgent.supervisor import SupervisorAgent
from Agent.MultiAgent.router import QueryClassifier
from Agent.MultiAgent.schemas import IntentType, IntentClassification, SupervisorDecision
from Agent.MultiAgent.SubAgent import (
    BaseSubAgent,
    ProductAgent,
    AfterSalesAgent,
    ChatAgent,
)

__all__ = [
    "SupervisorAgent",
    "BaseSubAgent",
    "ProductAgent",
    "AfterSalesAgent",
    "ChatAgent",
    "QueryClassifier",
    "IntentType",
    "IntentClassification",
    "SupervisorDecision",
]
