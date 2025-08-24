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
            self._handle_exception(response)
            return response.json()
        except ConnectionError as ex:
            msg = f"HYPERLIQUID，REST API连接断开，请求地址：{url}，错误信息：{ex}"
            write_log(msg)
            # SSL连接重试次数超限
            if "Max retries exceeded" in str(ex):
                save_connection_status("HYPERLIQUID",False,msg)
        except NameResolutionError as ex:
            msg = f"HYPERLIQUID，DNS解析失败，无法解析域名，请求地址：{url}，错误信息：{ex}"
            write_log(msg)
        except Timeout as ex:
            msg = f"HYPERLIQUID，请求超时，请求地址：{url}，错误信息：{ex}"
            write_log(msg)
        except ValueError:
            msg = f"HYPERLIQUID，REST API解析json出错，错误信息：{response.text}"
            write_log(msg)
            return {"error": msg}
        except Exception as ex:
            msg = f"HYPERLIQUID，REST API运行出错，请求地址：{url}，错误信息：{ex}"
            write_log(msg)
            save_connection_status("HYPERLIQUID",False,msg)

    def _handle_exception(self, response):
        status_code = response.status_code
        if status_code < 400:
            return
        else:
            text = response.text
            if text == "null":
                return
            msg = f"HYPERLIQUID，REST API请求失败，请求地址：{response.url}，错误代码：{status_code}，错误信息：{text}"
            write_log(msg)
            # 502 bad gateway重启交易子进程
            # if status_code in [502]:
                # save_connection_status("HYPERLIQUID",False,msg)

