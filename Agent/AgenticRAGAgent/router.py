"""
Agent/AgenticRAGAgent 智能路由
==============================

参照 UltimateRAG stage_3/router.py，裁剪为两个分支：
- knowledge_base : 进入自反思 RAG 闭环
- direct_answer  : 问候/闲聊/常识，不检索直接回答
"""
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from Agent.AgenticRAGAgent.prompts import DIRECT_ANSWER_PROMPT, ROUTER_PROMPT
from Agent.AgenticRAGAgent.schemas import RouteDecision, RouteType
from Agent.llm_client import create_chat_model
from config import get_config
from utils.logger_loguru import get_logger

logger = get_logger("AgenticRAGAgent.router")


class QueryRouter:
    """问题路由器：结构化输出二选一，失败时回退知识库检索"""

    def __init__(self):
        """
        Args:
            router: 绑定 RouteDecision 的结构化输出链（低温度 LLM）
            chat_llm: 普通对话 LLM，用于闲聊直答
        """
        self._router = create_chat_model(temperature=get_config("llm.eval_temperature", 0.1)).with_structured_output(RouteDecision, include_raw=True)
        self._chat_llm = create_chat_model(temperature=get_config("llm.generate_temperature", 0.3))

    async def route(self, question: str) -> dict[Any, Any] | RouteDecision | Any:
        """智能路由，失败时回退到知识库检索"""
        prompt = ChatPromptTemplate.from_template(ROUTER_PROMPT)
        try:
            raw_result = await self._router.ainvoke(prompt.format(question=question))
            decision = raw_result["parsed"]
            if decision is None:
                raise ValueError("parsed 结果为 None")
            return decision
        except Exception as e:
            logger.warning(f"路由决策失败，回退到知识库: error_type={type(e).__name__}")
            return RouteDecision(
                route_type=RouteType.KNOWLEDGE_BASE,
                confidence=0.5,
                reasoning="路由决策失败，使用默认知识库检索",
                transformed_query=question,
            )

    async def direct_answer(self, question: str, history_text: str) -> str:
        """闲聊/常识直接回答，不走知识库"""
        prompt = ChatPromptTemplate.from_template(DIRECT_ANSWER_PROMPT)
        response = await self._chat_llm.ainvoke(
            prompt.format(history=history_text or "无", question=question)
        )
        return response.content


