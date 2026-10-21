"""
商品咨询子 Agent
================

专注商品相关问题：成分/规格/价格查询、商品推荐、商品卡片发送。
工具集：self_rag_search, get_product_knowledge, get_shop_products, send_goods_link
"""
from Agent.tool_decorator import get_tools_by_names
from Agent.MultiAgent.prompts import PRODUCT_AGENT_SYSTEM_PROMPT
from Agent.MultiAgent.SubAgent.base import BaseSubAgent


class ProductAgent(BaseSubAgent):
    """商品咨询子 Agent：专注商品相关问题"""

    def _get_system_prompt(self) -> str:
        return PRODUCT_AGENT_SYSTEM_PROMPT

    def _get_tools(self) -> list:
        return get_tools_by_names([
            "self_rag_search",
            "get_product_knowledge",
            "get_shop_products",
            "send_goods_link",
        ])
