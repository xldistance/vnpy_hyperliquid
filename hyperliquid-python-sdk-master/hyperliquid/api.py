import json
import logging
from json import JSONDecodeError
from urllib3.exceptions import NameResolutionError
import requests
from requests.exceptions import Timeout,ConnectionError
from hyperliquid.utils.constants import MAINNET_API_URL
from hyperliquid.utils.error import ClientError, ServerError
from hyperliquid.utils.types import Any
from vnpy.trader.utility import save_connection_status,write_log

class API:
    def __init__(self, base_url=None, timeout=None):
        self.base_url = base_url or MAINNET_API_URL
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._logger = logging.getLogger(__name__)
        self.timeout = timeout

    def post(self, url_path: str, payload: Any = None) -> Any:
        payload = payload or {}
        url = self.base_url + url_path
        try:
            response = self.session.post(url, json=payload, timeout=self.timeout)
            status_code = response.status_code
            if status_code // 100 == 2:
                if status_code == 204:
                    json_body = {}
                else:
                    json_body = response.json()
                return json_body
            else:
                text = response.text
                # 收到null错误返回空字典
                if text == "null":
                    return {}
                # 502错误，重启交易子进程
                msg = f"REST API请求失败，请求地址：{response.url}，错误代码：{status_code}，错误信息：{text}"
                write_log(msg,"HYPERLIQUID")
                if status_code == 502:
                    save_connection_status("HYPERLIQUID",False,msg)
                return {"error":text}
        except ConnectionError as ex:
            msg = f"REST API连接断开，请求地址：{url}，错误信息：{ex}"
            write_log(msg,"HYPERLIQUID")
            # SSL连接重试次数超限
            if "Max retries exceeded" in str(ex):
                save_connection_status("HYPERLIQUID",False,msg)
        except NameResolutionError as ex:
            msg = f"DNS解析失败，无法解析域名，请求地址：{url}，错误信息：{ex}"
            write_log(msg,"HYPERLIQUID")
        except Exception as ex:
            msg = f"REST API运行出错，请求地址：{url}，错误信息：{ex}"
            write_log(msg,"HYPERLIQUID")
            save_connection_status("HYPERLIQUID",False,msg)

