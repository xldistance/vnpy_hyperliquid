## 📋 项目概述

本项目实现了 VNPY量化交易平台与 Hyperliquid 去中心化永续合约交易所的深度集成，通过官方 Python SDK 提供完整的交易功能支持，**支持同时交易期货和现货**。

## 🛠️ 安装依赖

### 核心依赖安装

```bash
cd hyperliquid-python-sdk-master
pip install .
```
### 🚫 重要
> **注意**:默认使用代理api交易，去官方网页/更多/api里面创建代理api，不建议使用私钥交易避免私钥泄露。要使用私钥交易，赋值self.use_api_agent为False。
> account_address是钱包的公开地址，private_address是代理api的私钥地址

### 🚫 架构说明

> **注意**: VNPY 原生的 REST API 和 WebSocket API 在此集成中**仅作为接口框架存在**，实际的数据交互完全通过 Hyperliquid 官方 SDK 实现。

> **注意**: 推荐安装我修改过的Hyperliquid Python SDK,自定义函数说明如下
```python
import pyjson5 as jsonc
import json5 as json
from filelock import FileLock
# 需要在write_log同文件中的某个函数传入交易子进程的EVENT_ENGINE并赋值全局变量，觉得太麻烦可以改成print或者logger
EVENT_ENGINE = EventEngine(interval = int(1e9))
EVENT_ENGINE.start()
def write_log(msg: str, gateway_name: str = ""):
    """
    写入日志信息
    """
    global EVENT_ENGINE
    data = LogData(msg=msg, gateway_name=gateway_name)
    event = Event(EVENT_LOG, data)
    EVENT_ENGINE.put(event)
def save_connection_status(gateway_name: str, status: bool, msg: str = ""):
    """
    * 保存交易接口连接状态
    * 参数说明:
        gateway_name: 交易接口的名称。
        status: 交易接口的连接状态，True 表示已连接，False 表示未连接。
        msg: 可选参数，用于记录与当前操作相关的任何日志信息。
    """
    # gateway_name为空值直接返回
    if not gateway_name:
        return
    if msg:
        write_log(msg)
    connection_status = load_json("connection_status.json")
    connection_status.update({gateway_name: status})
    save_json("connection_status.json", connection_status)

from dingtalkchatbot.chatbot import DingtalkChatbot
from datetime import datetime
class SendMessage:
    """
    * 钉钉发送信息
    * 过滤重复消息
    """
    # 信息推送webhook与密匙
    info_webhook_1: str = "https://oapi.dingtalk.com/robot/send?access_token=xxx"
    info_webhook_2: str = "https://oapi.dingtalk.com/robot/send?access_token=xxx"
    info_secret: str = "xxx"
    # 错误推送webhook与密匙
    error_webhook_1: str = "https://oapi.dingtalk.com/robot/send?access_token=xxx"
    error_secret: str = "xxx"

    def __init__(self, webhook: str, secret: str):
        # 初始化机器人小丁
        self.xiaoding = DingtalkChatbot(webhook, secret)
        self.last_text: str = ""  # 缓存钉钉发送的信息
    # ----------------------------------------------------------------------------------------------------
    def send_text(self, text: str):
        """
        text消息，is_at_all=True @所有人
        """
        # 过滤发送相同信息
        if self.last_text == text:
            return
        try:
            self.xiaoding.send_text(msg=f"【监控时间：{datetime.now(TZ_INFO)}】" + "\n" + f"{text}")
        except Exception as err:
            write_log(f"钉钉发送消息失败，错误信息：{err}")
        self.last_text = text
    # ----------------------------------------------------------------------------------------------------
    def send_image(self, url: str):
        """
        发送网络图片
        """
        self.xiaoding.send_image(pic_url=f"{url}")
# ----------------------------------------------------------------------------------------------------
# 钉钉推送日志信息
info_webhook = SendMessage.info_webhook_1
info_secret = SendMessage.info_secret
info_monitor = SendMessage(info_webhook, info_secret)
# ----------------------------------------------------------------------------------------------------
# 钉钉推送错误信息
error_webhook = SendMessage.error_webhook_1
error_secret = SendMessage.error_secret
error_monitor = SendMessage(error_webhook, error_secret)
