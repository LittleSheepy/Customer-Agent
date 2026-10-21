import json
from typing import Any, Callable, Dict, List, Optional

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from config import get_config
from utils.logger_loguru import get_logger

logger = get_logger("Agent.utils")


# ======================================================================
# 不可信数据清理与 Prompt 注入防护
# ======================================================================

def sanitize_text(text: Any, max_length: int = 2000) -> str:
    """清理不可信文本：替换尖括号、去除空字节、截断

    所有来自外部（用户输入、商品目录、摘要等）的文本在注入消息前
    必须经过此函数，防止 prompt injection。
    """
    return (
        str(text or "")
        .replace("<", "＜")
        .replace(">", "＞")
        .replace("\x00", "")[:max_length]
    )


from Agent.prompts_utils import (
    SUMMARY_WRAPPER_PREFIX, SUMMARY_WRAPPER_SUFFIX,
    MESSAGE_WRAPPER_PREFIX, MESSAGE_WRAPPER_SUFFIX,
)


def format_history_messages(
    history: List[Dict[str, Any]],
    dep_max: int = 2000,
) -> List[Dict[str, Any]]:
    """将持久化历史逐条结构化还原为 LLM 消息列表。

    - tool 消息：保留 tool_call_id 协议字段
    - summary/system 历史：sanitize + SUMMARY_WRAPPER 包裹，降级为 user 角色
    - assistant 消息：尝试从 JSON 恢复 tool_calls
    - user 消息：直接保留
    - 未知角色：sanitize + MESSAGE_WRAPPER 包裹，降级为 user 角色

    所有不可信内容均经 sanitize + wrapper 处理，防止 prompt injection。
    """
    messages: List[Dict[str, Any]] = []

    for msg in history:
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "tool":
            messages.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id", ""),
                "content": content,
            })
        elif role in ("system", "summary"):
            safe_content = sanitize_text(content, dep_max)
            messages.append({
                "role": "user",
                "content": (
                    f"{SUMMARY_WRAPPER_PREFIX}"
                    f"{safe_content}"
                    f"{SUMMARY_WRAPPER_SUFFIX}"
                ),
            })
        elif role == "assistant":
            # assistant 消息可能以 JSON 持久化（含 tool_calls），尝试恢复
            try:
                payload = json.loads(content) if isinstance(content, str) else None
            except (TypeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and payload.get("tool_calls"):
                messages.append({
                    "role": "assistant",
                    "content": payload.get("content", ""),
                    "tool_calls": payload["tool_calls"],
                })
            else:
                messages.append({"role": "assistant", "content": content})
        elif role == "user":
            messages.append({"role": "user", "content": content})
        else:
            # 未知角色：不可信数据，sanitize + wrapper 包裹
            safe_content = sanitize_text(content, dep_max)
            messages.append({
                "role": "user",
                "content": (
                    f"{MESSAGE_WRAPPER_PREFIX}"
                    f"{safe_content}"
                    f"{MESSAGE_WRAPPER_SUFFIX}"
                ),
            })

    # 修复 tool_calls / tool 配对：丢弃不完整组，防止孤立 tool_calls 触发 OpenAI 400
    return _repair_tool_pairing(messages)


def _repair_tool_pairing(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """修复历史消息中的 tool_calls / tool 配对，丢弃不完整组。

    OpenAI 协议要求：带 tool_calls 的 assistant 消息之后必须紧跟覆盖其全部
    tool_call_id 的 tool 消息，否则报 400。历史可能因触顶收尾、进程崩溃、
    超时取消等留下“孤立 tool_calls”或“孤立 tool 响应”，此函数在还原历史时
    统一清理，保证喂给 LLM 的消息序列合法。

    规则：
    - assistant(tool_calls)：仅当其后紧跟的 tool 消息覆盖了全部 tool_call_id 时保留，
      否则丢弃该 assistant 及其后不完整的 tool 消息。
    - 无前置 assistant(tool_calls) 的孤立 tool 消息：丢弃。
    """
    repaired: List[Dict[str, Any]] = []
    i, n = 0, len(messages)
    while i < n:
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            required = {tc.get("id") for tc in msg["tool_calls"] if tc.get("id")}
            # 收集紧随其后的连续 tool 消息
            j = i + 1
            following: List[Dict[str, Any]] = []
            got_ids = set()
            while j < n and messages[j].get("role") == "tool":
                following.append(messages[j])
                got_ids.add(messages[j].get("tool_call_id"))
                j += 1
            if required and required <= got_ids:
                # 配对完整：保留 assistant + 对应 tool 响应（过滤掉多余 id）
                repaired.append(msg)
                repaired.extend(t for t in following if t.get("tool_call_id") in required)
            else:
                logger.warning(
                    f"丢弃不完整的 tool_calls 组: required={required}, got={got_ids}"
                )
            i = j
            continue
        if msg.get("role") == "tool":
            # 孤立 tool（前面没有配对的 assistant tool_calls）→ 丢弃
            logger.warning("丢弃孤立的 tool 消息（无前置 tool_calls）")
            i += 1
            continue
        repaired.append(msg)
        i += 1
    return repaired


def messages2content(messages: List[Dict[str, Any]]) -> str:
    content = "\n".join(
        f"[{msg.get('role', 'unknown')}]: "
        f"{str(msg.get('content', ''))[:200]}"
        for msg in messages
        if msg.get("content")
    )
    return content


def format_documents(documents: List[Document]) -> str:
    """把文档列表格式化为提示词上下文"""
    parts = []
    for index, doc in enumerate(documents, 1):
        source = doc.metadata.get("source", "未知来源")
        parts.append(f"[文档 {index}] (来源: {source})\n{doc.page_content.strip()}")
    return "\n\n---\n\n".join(parts)


def to_documents(
    products: List[Any],
    cs_list: List[Any],
) -> List[Document]:
    """把 KnowledgeService 的 ORM 对象列表转为 LangChain Document。

    模块级函数：向量索引（vector_store.py）与关键词检索共用同一格式，
    确保混合检索合并去重时 page_content 可直接比对。
    """
    docs: List[Document] = []

    for product in products:
        lines = [f"商品名称：{product.goods_name or ''}"]
        if product.price:
            lines.append(f"价格：{product.price}")
        if product.extracted_content:
            lines.append(str(product.extracted_content))
        docs.append(Document(
            page_content="\n".join(lines),
            metadata={"type": "product", "source": product.goods_name or "产品知识"},
        ))

    for knowledge in cs_list:
        docs.append(Document(
            page_content=f"{knowledge.title or ''}\n{knowledge.content or ''}",
            metadata={"type": "customer_service", "source": knowledge.title or "客服知识"},
        ))

    return docs


def to_langchain_messages(messages: List[Dict[str, Any]]) -> list:
    """将 dict 消息列表转为 LangChain Message 对象列表。"""
    lc_messages = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            if msg.get("tool_calls"):
                lc_messages.append(AIMessage(
                    content=content,
                    tool_calls=[
                        {
                            "name": tc["function"]["name"],
                            "args": json.loads(tc["function"]["arguments"])
                            if isinstance(tc["function"]["arguments"], str)
                            else tc["function"]["arguments"],
                            "id": tc["id"],
                        }
                        for tc in msg["tool_calls"]
                    ],
                ))
            else:
                lc_messages.append(AIMessage(content=content))
        elif role == "tool":
            lc_messages.append(ToolMessage(
                content=content,
                tool_call_id=msg.get("tool_call_id", ""),
            ))
        else:
            logger.warning(f"to_langchain_messages: 未知消息 role={role!r}，已跳过")
    return lc_messages


def format_history(
    history: List[Dict[str, Any]],
    limit: int = None,
) -> str:
    """把持久化历史转为简明对话文本（只取最近 limit 条）"""
    if limit is None:
        limit = get_config("self_rag.history_limit", 20)
    lines = []
    for message in history:
        role = message.get("role")
        content = (message.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"用户：{content}")
        elif role == "assistant":
            lines.append(f"客服：{content}")
        elif role == "summary":
            lines.append(f"对话摘要：{content}")
    return "\n".join(lines[-limit:])
