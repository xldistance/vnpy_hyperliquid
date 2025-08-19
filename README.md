## 📋 项目概述

本项目实现了 VNPY量化交易平台与 Hyperliquid 去中心化永续合约交易所的深度集成，通过官方 Python SDK 提供完整的交易功能支持，**支持同时交易期货和现货**。

## 🔗 官方资源

- **Hyperliquid 官方 Python SDK**: [https://github.com/hyperliquid-dex/hyperliquid-python-sdk](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)
- **SDK 文档**: 完整的 API 文档和使用示例

## 🛠️ 安装依赖

### 核心依赖安装

```bash
pip install hyperliquid-python-sdk
```
### 🚫 架构说明

> **注意**: VNPY 原生的 REST API 和 WebSocket API 在此集成中**仅作为接口框架存在**，实际的数据交互完全通过 Hyperliquid 官方 SDK 实现。

> **注意**: 安装完hyperliquid-python-sdk需要删除hyperliquid\websocket_manager.py文件里面的以下代码
```python
            if identifier == "userEvents" or identifier == "orderUpdates":
                # TODO: ideally the userEvent and orderUpdates messages would include the user so that we can multiplex
                if len(self.active_subscriptions[identifier]) != 0:
                    raise NotImplementedError(f"Cannot subscribe to {identifier} multiple times")
```

> **注意**: account_address为钱包地址,eth_private_address为钱包私钥地址
