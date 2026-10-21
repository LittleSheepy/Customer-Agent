from ..base_request import BaseRequest
from typing import Dict, Any


class SendMessage(BaseRequest):
    def __init__(self, shop_id: str, user_id: str, channel_name: str = "pinduoduo"):
        super().__init__(shop_id, user_id, channel_name)
        
        # 检查账户信息是否正确加载
        if not hasattr(self, 'account_name'):
            self.logger.error(f"无法在数据库中找到账户: shop_id={shop_id}, user_id={user_id}")
            raise ValueError("找不到指定的账户信息")

    def send_text(self, recipient_uid, message_content):
        """
        发送文本消息
        """
        url = "https://mms.pinduoduo.com/plateau/chat/send_message"
        data = {
            "data": {
                "cmd": "send_message",
                "request_id": self.generate_request_id(),
                "message": {
                    "to": {
                        "role": "user",
                        "uid": recipient_uid
                    },
                    "from": {
                        "role": "mall_cs"
                    },
                    "content": message_content,
                    "msg_id": None,
                    "type": 0,
                    "is_aut": 0,
                    "manual_reply": 1,
                },
            },
            "client": "WEB"
        }

        result = self.post(url, json_data=data)

        # 记录原始响应（脱敏），便于排查“外层 success=true 但实际未投递”的假成功
        self.logger.debug(f"send_text 响应: {self._sanitize_for_log(result)}")

        # 1. 外层 success 必须为 True
        if not (result and result.get("success") is True):
            self.logger.error(
                "发送文本消息失败: "
                f"response_keys={sorted(result.keys()) if isinstance(result, dict) else []}"
            )
            return None

        # 2. PDD 成功码为顶层 error_code == 1000000（见接口响应样例）。
        # 外层 success=true 但 error_code 非成功码时，多为风控拦截 / 会话异常，
        # 消息并未真正投递，必须判为失败；否则上层会误记“发送成功”，
        # 客户却收不到（表现为首次假成功、二次才成功）。
        # 保守起见：error_code 缺省或为 0/1000000 均视为成功，避免误伤正常发送。
        error_code = result.get("error_code")
        if error_code is not None and error_code not in (0, 1000000):
            self.logger.error(
                f"发送文本消息未投递: error_code={error_code}, "
                f"error_msg={result.get('error_msg')}"
            )
            return None

        # 3. 兼容内层 result.error_code（如平台侧 10002）：非成功码同样判为失败
        inner = result.get("result")
        inner_code = inner.get("error_code") if isinstance(inner, dict) else None
        if inner_code not in (None, 0, 1000000):
            self.logger.error(
                f"发送文本消息未投递: inner_error_code={inner_code}"
            )
            return None

        return result

 
        
    def send_image(self, recipient_uid, image_url):
        """
        发送图片消息
        """
        url = "https://mms.pinduoduo.com/plateau/chat/send_message"
        data = {
            "data": {
                "cmd": "send_message",
                "request_id": self.generate_request_id(),
                "message": {
                    "to": {
                        "role": "user",
                        "uid": recipient_uid
                    },
                    "from": {
                        "role": "mall_cs"
                    },
                    "content": image_url,
                    "msg_id": None,
                    "chat_type": "cs",
                    "type": 1,
                    "is_aut": 0,
                    "manual_reply": 1,
                }
            },
            "client": "WEB"
        }

        result = self.post(url, json_data=data)
        if result:
            self.logger.debug("发送图片消息完成")
            return result


    def send_mallGoodsCard(self, recipient_uid, goods_id, biz_type: int = 2):
        """
        发送商城商品卡片消息

        Args:
            recipient_uid: 接收消息的用户UID
            goods_id: 商品ID
            biz_type: 业务类型，默认2（客服推荐商品）
        """
        url = "https://mms.pinduoduo.com/plateau/message/send/mallGoodsCard"
        data = {
            "uid": recipient_uid,
            "goods_id": goods_id,
            "biz_type": biz_type
        }

        # anti-content 从 cookies 中获取（由后端动态生成）
        anti_content = self.cookies.get('anti_content') or self.cookies.get('anti-content', '')

        # 构建完整请求头
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "anti-content": anti_content,
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://mms.pinduoduo.com",
            "priority": "u=1, i",
            "referer": "https://mms.pinduoduo.com/chat-merchant/index.html",
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        }

        result = self.post(url, json_data=data, headers=headers)
        if result:
            if result.get("success"):
                self.logger.info(
                    f"商品卡片发送成功: goods_id={goods_id}, biz_type={biz_type}"
                )
            else:
                self.logger.error(
                    "商品卡片发送失败: "
                    f"response_keys={sorted(result.keys())}"
                )
            return result


    def getAssignCsList(self):
        """
        获取分配的客服列表
        """
        url = "https://mms.pinduoduo.com/latitude/assign/getAssignCsList"
        data = {"wechatCheck": True}
        
        result = self.post(url, json_data=data)
        if result and result.get('success'):
            return result['result']['csList']
        else:
            self.logger.error(
                "获取分配的客服列表失败: "
                f"response_keys={sorted(result.keys()) if isinstance(result, dict) else []}"
            )
            return None


    def move_conversation(self, recipient_uid, cs_uid):
        """
        转移会话
        """
        url = "https://mms.pinduoduo.com/plateau/chat/move_conversation"
        data = {
            "data": {
                "cmd": "move_conversation",
                "request_id": self.generate_request_id(),
                "conversation": {
                    "csid": cs_uid,
                    "uid": recipient_uid,
                    "need_wx": False,
                    "remark": "无原因直接转移"
                }
            },
            "client": "WEB"
        }
        
        result = self.post(url, json_data=data)
        if result:
            self.logger.debug("转移会话请求完成")
            return result
