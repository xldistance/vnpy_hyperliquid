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

### 🚫 架构说明

> **注意**: VNPY 原生的 REST API 和 WebSocket API 在此集成中**仅作为接口框架存在**，实际的数据交互完全通过 Hyperliquid 官方 SDK 实现。

> **注意**: 推荐安装我修改过的Hyperliquid Python SDK,自定义函数说明如下
```python
import pyjson5 as jsonc
import json5 as json
from filelock import FileLock
# 需要在write_log同文件中的某个函数传入交易子进程的EVENT_ENGINE并赋值全局变量，觉得太麻烦可以改成print或者logger
EVENT_ENGINE = EventEngine()
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
def load_json(filename: str) -> Dict:
    """
    读取json文件
    """
    filepath = get_file_path(filename)
    if not filepath.exists():
        save_json(filename, {})
        return {}

    lock_file = filepath.with_suffix(filepath.suffix + ".lock")
    with FileLock(lock_file):
