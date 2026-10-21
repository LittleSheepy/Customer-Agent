"""
售后服务子 Agent
================

专注售后相关问题：退换货、物流查询、投诉处理、转人工。
工具集：self_rag_search, search_customer_service_knowledge, transfer_conversation
"""
from Agent.tool_decorator import get_tools_by_names
from Agent.MultiAgent.prompts import AFTER_SALES_AGENT_SYSTEM_PROMPT
from Agent.MultiAgent.SubAgent.base import BaseSubAgent


class AfterSalesAgent(BaseSubAgent):
    """售后服务子 Agent：专注售后相关问题"""

    def _get_system_prompt(self) -> str:
        return AFTER_SALES_AGENT_SYSTEM_PROMPT

    def _get_tools(self) -> list:
        return get_tools_by_names([
            "self_rag_search",
            "search_customer_service_knowledge",
            "transfer_conversation",
        ])
