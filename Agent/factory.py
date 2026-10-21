"""
Agent 工厂 — 按配置名创建 Agent 实例

各 Agent 类型延迟导入，避免循环依赖。
新增 Agent 类型只需在 _AGENT_REGISTRY 中添加一条映射。
"""

from __future__ import annotations

from typing import Any

from config import get_config
from utils.logger_loguru import get_logger

logger = get_logger("Agent.factory")

# Agent 名称 → 模块路径 + 类名（延迟导入，避免循环依赖）
_AGENT_REGISTRY: dict[str, tuple[str, str]] = {
    "agentic": ("Agent.AgenticRAGAgent", "AgenticRAGAgent"),
    "multi": ("Agent.MultiAgent", "SupervisorAgent"),
}


def create_account_agent(account_info: dict[str, Any] | None = None) -> Any:
    """根据 config.json 的 agent.active 配置创建对应的 Agent 实例

    Args:
        account_info: 账号信息字典，传递给 Agent 构造函数（可选）

    Returns:
        对应的 Agent 实例

    Raises:
        ValueError: 未知的 Agent 类型
    """
    agent_type = get_config("agent.active", "multi")
    entry = _AGENT_REGISTRY.get(agent_type)
    if entry is None:
        available = ", ".join(_AGENT_REGISTRY)
        raise ValueError(f"未知 Agent 类型: {agent_type!r}，可选: {available}")

    module_path, cls_name = entry
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, cls_name)

    agent = cls(account_info=account_info)

    logger.debug(f"创建 Agent: type={agent_type}, class={cls.__name__}")
    return agent
