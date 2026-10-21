"""
Agent/MultiAgent/SubAgent - 子 Agent 模块

提供子 Agent 基类和三个专职子 Agent 实现。
"""

from Agent.MultiAgent.SubAgent.base import BaseSubAgent
from Agent.MultiAgent.SubAgent.product_agent import ProductAgent
from Agent.MultiAgent.SubAgent.after_sales_agent import AfterSalesAgent
from Agent.MultiAgent.SubAgent.chat_agent import ChatAgent

__all__ = [
    "BaseSubAgent",
    "ProductAgent",
    "AfterSalesAgent",
    "ChatAgent",
]
