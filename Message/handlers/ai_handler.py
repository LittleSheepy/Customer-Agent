"""
AI回复处理器
专注的AI处理，移除复杂预处理和发送逻辑
"""

import asyncio
from typing import Dict, Any, Optional
from bridge.context import Context, ContextType
from .base import BaseHandler
from .preprocessor import MessagePreprocessor
from Agent.AgentBase import AgentBase


class AIReplyHandler(BaseHandler):
    """专注的AI回复处理器"""

    def __init__(self, bot: AgentBase = None, auto_reply_types: set = None, message_callback=None):
        super().__init__("AIReplyHandler")
        self.bot = bot
        self.preprocessor = MessagePreprocessor()
        self.message_callback = message_callback  # 用于通知 UI 新消息
        self.auto_reply_types = auto_reply_types or {
            ContextType.TEXT,
            ContextType.GOODS_INQUIRY,
            ContextType.GOODS_SPEC,
            ContextType.ORDER_INFO,
            ContextType.IMAGE,
            ContextType.VIDEO,
            ContextType.EMOTION
        }

    def can_handle(self, context: Context) -> bool:
        """检查是否可以处理该消息"""
        # 支持多种消息类型
        return context.type in self.auto_reply_types

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        """处理AI回复"""
        try:
            # 1. 预处理消息
            processed_content = self.preprocessor.process(context.content, context.type)

            # 2. 调用AI生成回复
            reply = await self._get_ai_reply(processed_content, context)
            if not reply:
                self.logger.warning("AI回复生成失败，使用备用回复")
                return await self._handle_fallback(context, metadata)

            # 3. 发送回复
            success = await self._send_reply(context, reply, metadata)
            if success:
                await self.log_message(context, "AI回复发送成功", f"回复: {reply}...")
            else:
                self.logger.warning("AI回复发送失败")
                return await self._handle_fallback(context, metadata)

            return True

        except Exception as e:
            self.logger.error(
                f"AI回复处理失败: error_type={type(e).__name__}"
            )
            return await self._handle_fallback(context, metadata)

    async def _get_ai_reply(self, query: str, context: Context) -> Optional[str]:
        """获取AI回复"""
        if not self.bot:
            return None

        try:
            res = await self.bot.async_reply(query, context)
            return getattr(res, 'content', str(res))

        except Exception as e:
            self.logger.error(
                f"AI Bot调用失败: error_type={type(e).__name__}"
            )
            return None

    async def _send_reply(self, context: Context, reply: str, metadata: Dict[str, Any]) -> bool:
        """发送回复"""
        try:
            # 从metadata中提取必要信息
            shop_id = metadata.get('shop_id')
            user_id = metadata.get('user_id')
            from_uid = metadata.get('from_uid')

            if not all([shop_id, user_id, from_uid]):
                self.logger.warning(f"缺少发送信息: shop_id={shop_id}, user_id={user_id}, from_uid={from_uid}")
                return False

            # 通过发送器抽象发送（同步 HTTP + DB，放工作线程避免阻塞事件循环）
            from bridge.sender import get_sender
            sender = get_sender(context.channel_type)
            if not sender:
                self.logger.warning(f"无可用发送器: channel_type={context.channel_type}")
                return False
            result = await asyncio.to_thread(sender.send_text, shop_id, user_id, from_uid, reply)
            if isinstance(result, dict) and result.get("success"):
                # 发送成功后，通过回调通知 UI
                if self.message_callback:
                    try:
                        import time
                        account_data = {
                            "shop_id": shop_id,
                            "user_id": user_id,
                            "channel_name": "pinduoduo",
                            "shop_name": metadata.get("shop_name", ""),
                        }
                        self.message_callback(
                            account_data,
                            reply,
                            True,  # is_self=True (客服发送)
                            metadata.get("csname", "客服"),
                            time.time(),
                            from_uid
                        )
                    except Exception as cb_error:
                        self.logger.debug(f"消息回调通知失败: {cb_error}")
                return True
            return False

        except Exception as e:
            self.logger.error(
                f"发送回复失败: error_type={type(e).__name__}"
            )
            return False

    async def _handle_fallback(self, context: Context, metadata: Dict[str, Any]) -> bool:
        """备用回复处理"""
        try:
            # 简单的自动回复
            reply_text = "亲，感谢您的咨询！客服正在为您处理，请稍等片刻。"

            # 记录备用回复
            self.logger.info("使用备用回复")

            # 尝试发送备用回复
            success = await self._send_reply(context, reply_text, metadata)
            if not success:
                # 如果发送失败，记录日志并返回False让下游有机会处理
                await self.log_message(context, "备用回复发送失败", f"内容: {reply_text}")
                return False

            await self.log_message(context, "备用回复发送成功", f"内容: {reply_text}")
            return True

        except Exception as e:
            self.logger.error(
                f"备用回复处理失败: error_type={type(e).__name__}"
            )
            return False
