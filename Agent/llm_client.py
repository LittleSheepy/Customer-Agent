"""
Agent LLM 客户端工厂
====================

创建经配置验证的 ChatOpenAI 实例，并包装韧性层（熔断器 + 重试）。
内部先调用 build_llm_profile() 做完整验证（provider 枚举、端点安全、
工具能力检查），再用验证通过的参数创建 ChatOpenAI，
最后用 ResilientModel 包装以提供生产级容错能力。
"""
from langchain_openai import ChatOpenAI

from Agent.resilience import ResilientModel
from config import get_config, get_validated_config
from utils.llm_provider import build_llm_profile


def create_chat_model(temperature: float) -> ResilientModel:
    """创建带韧性保护的 LLM 实例

    通过 build_llm_profile 完成配置验证，创建 ChatOpenAI，
    最后用 ResilientModel 包装以提供：
    - 熔断器：连续失败达阈值后快速失败，防止雪崩
    - 指数退避重试：0.5s → 1s，瞬时故障自动恢复

    Returns:
        ResilientModel 包装的 LLM 实例（接口兼容原 ChatOpenAI.ainvoke/bind_tools）
    """
    profile = build_llm_profile(
        get_validated_config().llm.model_dump(),
        require_api_key=True,
        require_confirmation=False,
    )
    base_model = ChatOpenAI(
        model=profile.model_name,
        api_key=profile.api_key,
        base_url=profile.effective_api_base or None,
        temperature=temperature,
        extra_body={"enable_thinking": False},
    )

    # 韧性层参数由 config.json 配置，有合理默认值
    return ResilientModel(
        model=base_model,
        max_retries=get_config("agent.llm_max_retries", 2),
        backoff_base=get_config("agent.llm_backoff_base", 0.5),
        failure_threshold=get_config("agent.llm_failure_threshold", 5),
        recovery_timeout=get_config("agent.llm_recovery_timeout", 60.0),
    )
