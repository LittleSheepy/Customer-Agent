import json

from ..base_request import BaseRequest
import time


class GetMessages(BaseRequest):
    # 每页消息数量
    PAGE_SIZE = 100

    def __init__(self, cookies=None):
        # 如果直接传入cookies，不需要从数据库获取
        super().__init__()
        if cookies:
            self.update_cookies(cookies)
    
    def get_messages(self, uid, captured_anti_content, page_num=1):
        """获取单页消息"""
        url = "https://mms.pinduoduo.com/latitude/search/message/getMessages"
        now = time.localtime()
        base = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0,
                        now.tm_wday, now.tm_yday, now.tm_isdst))
        start_time = int(base) - 30 * 86400
        end_time = int(base) + 86399  # 23:59:59
        payload = {
            "pageNum": page_num,
            "pageSize": self.PAGE_SIZE,
            "startTime": start_time,
            "endTime": end_time,
            "keywords": "",
            "uid": int(uid) if uid else None,
        }
        # anti-content 从 cookies 中获取（由后端动态生成），如果传入了则使用传入的
        anti_content = captured_anti_content or self.cookies.get('anti_content') or self.cookies.get('anti-content', '')
        headers = {
            "anti-content": anti_content
        }
        result = self.post(url, json_data=payload, headers=headers)

        
        if result and result.get("success") == True:
            result_data = result.get('result', {})
            return result_data
        else:
            error_msg = result.get('error_msg') if result else "获取店铺信息失败"
            self.logger.error(f"获取店铺信息失败: {error_msg}")
            return False

    def get_all_messages(self, uid, captured_anti_content):
        """获取所有消息（自动分页）"""
        all_messages = []
        user_info = None
        page_num = 1
        
        while True:
            result = self.get_messages(uid, captured_anti_content, page_num)
            if result is False:
                return False
            
            # 保存用户信息（第一页获取）
            if user_info is None:
                user_info = result.get("userInfo", {})
            
            # 获取当前页消息
            content_list = result.get("contentList", [])
            all_messages.extend(content_list)
            
            # 检查是否还有更多页
            total = result.get("total", 0)
            fetched = len(all_messages)
            
            if fetched >= total or len(content_list) < self.PAGE_SIZE:
                break
            
            page_num += 1
        
        return {
            "userInfo": user_info,
            "contentList": all_messages,
            "total": len(all_messages)
        }