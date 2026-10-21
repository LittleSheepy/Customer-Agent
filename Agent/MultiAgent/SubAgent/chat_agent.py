"""
闲聊/问候子 Agent
==================

处理通用对话：问候、寒暄、感谢、道别、通用问题。
工具集：无（直接由 LLM 生成回复）
"""
from Agent.MultiAgent.prompts import CHAT_AGENT_SYSTEM_PROMPT
from Agent.MultiAgent.SubAgent.base import BaseSubAgent


class ChatAgent(BaseSubAgent):
    """闲聊/问候子 Agent：处理通用对话，无工具"""

    async def _on_initialize(self) -> None:
        await super()._on_initialize()
        self._max_loops = 1  # 闲聊不需要工具调用循环

    async def _get_rag_context(self, query, history, context=None) -> str:
        """闲聊不需要 RAG 检索"""
        return ""

    async def _prefetch_data(self, context=None) -> str:
        """闲聊不需要商品列表"""
        return ""

    def _get_system_prompt(self) -> str:
        return CHAT_AGENT_SYSTEM_PROMPT

    def _get_tools(self) -> list:
        return []  # 闲聊不需要工具
