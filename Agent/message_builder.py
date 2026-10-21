"""
通用消息构建器

负责构建 LLM 消息列表：系统提示词（含占位符替换）+ 会话信息注入
+ 不可信数据 wrapper 包裹 + 历史消息结构化还原 + 用户消息。

AgentBase 持有此类的实例，子类可按需配置高级功能（会话信息、商品目录等）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, OrderedDict

from config import get_config
from utils.logger_loguru import get_logger
from Agent.utils import sanitize_text, format_history_messages
from Agent.prompts_utils import (
    CATALOG_WRAPPER_PREFIX, CATALOG_WRAPPER_SUFFIX,
    BUSINESS_RULES,
)

logger = get_logger("Agent.MessageBuilder")


class MessageBuilder:
    """通用消息构建器

    基础用法（简单 Agent）：
        builder = MessageBuilder(system_prompt="你是一个客服。")
        messages = builder.build_messages(query, history)

    高级用法（配置会话信息、商品目录等）：
        builder = MessageBuilder(
            system_prompt="你好，我是{shop_name}的客服。",
            session_info_fields={"shop_id": "店铺ID", "user_id": "用户ID"},
            session_info_header="\\n【当前会话】\\n",
            session_info_footer="\\n请使用上面的参数调用工具。",
            catalog_wrapper=("＜untrusted_product_catalog＞\\n", "\\n＜/untrusted_product_catalog＞"),
        )
        messages = builder.build_messages(query, history, dependencies, catalog_payload)
    """

    def __init__(
        self,
        system_prompt: str = "",
        instructions: Optional[List[str]] = None,
        session_info_fields: Optional[OrderedDict[str, str]] = None,
        session_info_header: str = "\n【当前会话信息】\n",
        session_info_footer: str = "",
        catalog_wrapper: Optional[tuple] = None,
        catalog_key: Optional[str] = None,
        catalog_placeholder: str = "[产品目录见后续参考数据]",
        business_hours: Optional[Dict[str, str]] = None,
    ):
        """
        Args:
            system_prompt: 系统提示词模板，可含 {placeholder} 占位符
            instructions: 额外指令列表，追加到系统提示词末尾
            session_info_fields: 会话信息字段 {字段名: 描述}，None 表示不注入
            session_info_header: 会话信息头部文本
            session_info_footer: 会话信息尾部文本
            catalog_wrapper: (prefix, suffix) 不可信数据包装模板，None 表示不包装
            catalog_key: dependencies 中作为商品目录的键名（如 "product_list"），
                        该键会从 dependencies 中提取，作为不可信数据 wrapper 包裹，
                        系统提示词中对应占位符替换为 catalog_placeholder
            catalog_placeholder: 系统提示词中商品目录占位符的替换文本
            business_hours: 营业时间 {"start": "08:00", "end": "23:00"}，
                           None 表示不注入业务规则
        """
        self.system_prompt = system_prompt
        self.instructions = instructions or []
        self.session_info_fields = session_info_fields
        self.session_info_header = session_info_header
        self.session_info_footer = session_info_footer
        self.catalog_wrapper = catalog_wrapper
        self.catalog_key = catalog_key
        self.catalog_placeholder = catalog_placeholder
        self.business_hours = business_hours

    def build_messages(
        self,
        query: str,
        history: List[Dict[str, Any]],
        dependencies: Optional[Dict[str, Any]] = None,
        catalog_payload: str = "",
    ) -> List[Dict[str, Any]]:
        """构建 LLM 消息列表

        Args:
            query: 用户查询
            history: 历史消息列表
            dependencies: 依赖字典，用于系统提示词占位符替换
            catalog_payload: 不可信数据文本（如商品目录），经 wrapper 包裹后作为独立 user 消息

        Returns:
            LLM 消息列表
        """
        messages: List[Dict[str, Any]] = []
        dep_max = get_config("prompt.dependency_value_max_length", 2000)
        session_max = get_config("prompt.session_value_max_length", 256)

        # 从 dependencies 中提取商品目录（catalog_key 指定）
        if self.catalog_key and dependencies and self.catalog_key in dependencies:
            dependencies = dict(dependencies)  # 浅拷贝，不修改原字典
            catalog_text = str(dependencies.pop(self.catalog_key, ""))
            if catalog_text and not catalog_payload:
                catalog_payload = catalog_text
            # 系统提示词中的占位符替换为说明文本
            dependencies[self.catalog_key] = self.catalog_placeholder

        # 1. 系统提示词
        system_content = self._build_system_prompt(dependencies, dep_max, session_max)
        if system_content:
            messages.append({"role": "system", "content": system_content})

        # 2. 不可信数据（如商品目录）作为独立 user 消息，wrapper 包裹
        if catalog_payload and self.catalog_wrapper:
            catalog_max = get_config("prompt.catalog_max_length", 12000)
            safe_catalog = sanitize_text(catalog_payload, catalog_max)
            prefix, suffix = self.catalog_wrapper
            messages.append({
                "role": "user",
                "content": f"{prefix}{safe_catalog}{suffix}",
            })

        # 3. 历史消息：逐条结构化还原（共享函数）
        messages.extend(format_history_messages(history, dep_max))

        # 4. 当前用户消息
        messages.append({"role": "user", "content": query})

        return messages

    def _build_system_prompt(
        self,
        dependencies: Optional[Dict[str, Any]],
        dep_max: int,
        session_max: int,
    ) -> str:
        """构建系统提示词：模板 + 占位符替换 + 会话信息注入"""
        content = self.system_prompt
        if not content:
            return ""

        # 额外指令追加
        if self.instructions:
            content += "\n---\n" + "\n".join(f"- {i}" for i in self.instructions)

        # 占位符替换（sanitize 防注入）
        if dependencies:
            for key, value in dependencies.items():
                safe_value = sanitize_text(value, dep_max)
                content = content.replace(f"{{{key}}}", safe_value)

        # 会话信息注入
        if self.session_info_fields and dependencies:
            info = self.session_info_header
            for field, description in self.session_info_fields.items():
                info += (
                    f"- {field}: {sanitize_text(dependencies.get(field, ''), session_max)}"
                    f"（{description}）\n"
                )
            info += self.session_info_footer
            content += info

        # 业务规则注入（人工客服时间等）
        if self.business_hours:
            content += "\n\n" + BUSINESS_RULES.format(
                business_hours_start=self.business_hours.get("start", "08:00"),
                business_hours_end=self.business_hours.get("end", "23:00"),
            )

        return content
