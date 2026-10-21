from ..base_request import BaseRequest
import time


class GetMessagesUsers(BaseRequest):
    def __init__(self, cookies=None):
        # 如果直接传入cookies，不需要从数据库获取
        super().__init__()
        if cookies:
            self.update_cookies(cookies)
    
    def get_messages_users(self):
        url = "https://mms.pinduoduo.com/latitude/search/message/getMessagesUsers"
        now = time.localtime()
        base = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0,
                        now.tm_wday, now.tm_yday, now.tm_isdst))
        start_time = int(base) - 30 * 86400
        end_time = int(base) + 86399  # 23:59:59
        payload = {
            "pageNum": 1,
            "pageSize": 25,
            "startTime": start_time,
            "endTime": end_time,
            "keywords": "",
        }
        result = self.post(url, json_data=payload)
        
        if result and result.get("success") == True:
            result_data = result.get('result', {})
            return result_data
        else:
            error_msg = result.get('errorMsg') if result else "获取店铺信息失败"
            self.logger.error(f"获取店铺信息失败: {error_msg}")
            return False