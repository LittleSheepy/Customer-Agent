from ..base_request import BaseRequest


class GetCustomServiceInfo(BaseRequest):
    def __init__(self, cookies=None):

        super().__init__()
        if cookies:
            self.update_cookies(cookies)
    def get_custom_service_info(self) -> bool | dict:
        url = "https://mms.pinduoduo.com/janus/api/customService/queryCustomServiceInfo"

        result = self.post(url, json_data={"needCustomServiceInfo": True, "needMallInfo": True})
        
        if result and result.get("success") == True:
            result_data = result.get('result', {})
            return result_data
        else:
            error_msg = result.get('errorMsg') if result else "获取用户信息失败"
            self.logger.error(f"获取用户信息失败: {error_msg}")
            return False

"""
"result": {
    "customServiceInfo": {
      "chatNickName": "小美",
      "chatHeadImageUrl": "https:99b7a925-d2e0-4509-9e4b-3e69d50db13e.png"
    },
    "mallInfoResult": {
      "mallId": 926653581,
      "logo": "http:4dfb2729889b4327bc6f201242eef902.png",
      "mallName": "AI玄之又玄"
    }
  }
"""