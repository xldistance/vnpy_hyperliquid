import json
import logging
import threading
import time
from collections import defaultdict
import socket

import websocket

from hyperliquid.utils.types import Any, Callable, Dict, List, NamedTuple, Optional, Subscription, Tuple, WsMsg
from vnpy.trader.utility import save_connection_status, write_log

ActiveSubscription = NamedTuple("ActiveSubscription", [("callback", Callable[[Any], None]), ("subscription_id", int)])


def subscription_to_identifier(subscription: Subscription) -> str:
    if subscription["type"] == "allMids":
        return "allMids"
    elif subscription["type"] == "l2Book":
        return f'l2Book:{subscription["coin"].lower()}'
    elif subscription["type"] == "trades":
        return f'trades:{subscription["coin"].lower()}'
    elif subscription["type"] == "userEvents":
        return "userEvents"
    elif subscription["type"] == "userFills":
        return f'userFills:{subscription["user"].lower()}'
    elif subscription["type"] == "candle":
        return f'candle:{subscription["coin"].lower()},{subscription["interval"]}'
    elif subscription["type"] == "orderUpdates":
        return "orderUpdates"
    elif subscription["type"] == "userFundings":
        return f'userFundings:{subscription["user"].lower()}'
    elif subscription["type"] == "userNonFundingLedgerUpdates":
        return f'userNonFundingLedgerUpdates:{subscription["user"].lower()}'
    elif subscription["type"] == "webData2":
        return f'webData2:{subscription["user"].lower()}'
    elif subscription["type"] == "bbo":
        return f'bbo:{subscription["coin"].lower()}'
    elif subscription["type"] == "activeAssetCtx":
        return f'activeAssetCtx:{subscription["coin"].lower()}'
    elif subscription["type"] == "activeAssetData":
        return f'activeAssetData:{subscription["coin"].lower()},{subscription["user"].lower()}'


def ws_msg_to_identifier(ws_msg: WsMsg) -> Optional[str]:
    if ws_msg["channel"] == "pong":
        return "pong"
    elif ws_msg["channel"] == "allMids":
        return "allMids"
    elif ws_msg["channel"] == "l2Book":
        return f'l2Book:{ws_msg["data"]["coin"].lower()}'
    elif ws_msg["channel"] == "trades":
        trades = ws_msg["data"]
        if len(trades) == 0:
            return None
        else:
            return f'trades:{trades[0]["coin"].lower()}'
    elif ws_msg["channel"] == "user":
        return "userEvents"
    elif ws_msg["channel"] == "userFills":
        return f'userFills:{ws_msg["data"]["user"].lower()}'
    elif ws_msg["channel"] == "candle":
        return f'candle:{ws_msg["data"]["s"].lower()},{ws_msg["data"]["i"]}'
    elif ws_msg["channel"] == "orderUpdates":
        return "orderUpdates"
    elif ws_msg["channel"] == "userFundings":
        return f'userFundings:{ws_msg["data"]["user"].lower()}'
    elif ws_msg["channel"] == "userNonFundingLedgerUpdates":
        return f'userNonFundingLedgerUpdates:{ws_msg["data"]["user"].lower()}'
    elif ws_msg["channel"] == "webData2":
        return f'webData2:{ws_msg["data"]["user"].lower()}'
    elif ws_msg["channel"] == "bbo":
        return f'bbo:{ws_msg["data"]["coin"].lower()}'
    elif ws_msg["channel"] == "activeAssetCtx" or ws_msg["channel"] == "activeSpotAssetCtx":
        return f'activeAssetCtx:{ws_msg["data"]["coin"].lower()}'
    elif ws_msg["channel"] == "activeAssetData":
        return f'activeAssetData:{ws_msg["data"]["coin"].lower()},{ws_msg["data"]["user"].lower()}'


class WebsocketManager(threading.Thread):
    def __init__(self, base_url, auto_reconnect=True):
        super().__init__()
        self.subscription_id_counter = 0
        self.ws_ready = False
        self.queued_subscriptions: List[Tuple[Subscription, ActiveSubscription]] = []
        self.active_subscriptions: Dict[str, List[ActiveSubscription]] = defaultdict(list)
        self.all_subscriptions: List[Tuple[Subscription, ActiveSubscription]] = []  # 新增：保存所有订阅信息
        self.base_url = base_url
        ws_url = "ws" + base_url[len("http") :] + "/ws"
        self.ws_url = ws_url
        self.ws = None
        self.ping_sender = None
        self.ping_stop_event = threading.Event()  # 新增：独立的ping线程停止事件
        self.stop_event = threading.Event()
        self.auto_reconnect = auto_reconnect
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 1  # 重连延迟（秒）
        self.need_reconnect = False  # 新增：标记是否需要重连
        
        # 初始化WebSocket连接
        self._create_websocket()

    def _create_websocket(self):
        """创建新的WebSocket连接"""
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=self.on_message,
            on_open=self.on_open,
            on_error=self.on_error,
            on_close=self.on_close  # 启用on_close回调
        )
        
    def _stop_ping_thread(self):
        """停止ping线程"""
        if self.ping_sender and self.ping_sender.is_alive():
            self.ping_stop_event.set()  # 设置停止标志
            self.ping_sender.join(timeout=2)  # 等待线程结束，最多等2秒
            self.ping_sender = None
            self.ping_stop_event.clear()  # 清除标志以便下次使用

    def _start_ping_thread(self):
        """启动新的ping线程"""
        # 先停止旧的ping线程（如果存在）
        self._stop_ping_thread()
        
        # 创建并启动新的ping线程
        self.ping_sender = threading.Thread(target=self.send_ping, daemon=True)
        self.ping_sender.start()

    def _handle_reconnect(self):
        """处理重连逻辑"""
        if not self.auto_reconnect or self.stop_event.is_set():
            return False
            
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            msg = "WEBSOCKET API已达到最大重连次数"
            write_log(msg,"HYPERLIQUID")
            save_connection_status("HYPERLIQUID", False, msg)
            return False
            
        self.reconnect_attempts += 1
        wait_time = min(self.reconnect_delay * self.reconnect_attempts, 30)  # 指数退避，最大30秒
        
        write_log(f"WEBSOCKET API将在{wait_time}秒后尝试第{self.reconnect_attempts}次重连...","HYPERLIQUID")
        
        # 等待期间检查是否需要停止
        if self.stop_event.wait(wait_time):
            return False
            
        # 重新创建WebSocket实例
        self._create_websocket()
        self.ws_ready = False
        self.need_reconnect = False
        return True

    def run(self):
        """主运行循环，支持自动重连"""
        while not self.stop_event.is_set():
            try:
                # 运行WebSocket
                self.ws.run_forever()
                
                # 如果run_forever退出了，停止旧的ping线程
                self._stop_ping_thread()
                
                # 检查是否需要重连
                if not self.stop_event.is_set() and (self.need_reconnect or not self.is_connected):
                    if not self._handle_reconnect():
                        break
                        
            except Exception as e:
                msg = f"WEBSOCKET API运行时发生异常: {e}"
                write_log(msg,"HYPERLIQUID")
                self.is_connected = False
                self.need_reconnect = True
                
                # 停止旧的ping线程
                self._stop_ping_thread()
                
                if not self._handle_reconnect():
                    save_connection_status("HYPERLIQUID", False, msg)
                    break

    def send_ping(self):
        """发送心跳包"""
        while not self.stop_event.is_set() and not self.ping_stop_event.wait(30):
            if not self.is_connected or self.ws is None or not self.ws.keep_running:
                break
            try:
                self.ws.send(json.dumps({"method": "ping"}))
            except Exception as e:
                write_log(f"发送ping失败: {e}","HYPERLIQUID")
                self.is_connected = False
                self.need_reconnect = True
                break

    def stop(self):
        """停止WebSocket连接"""
        self.stop_event.set()
        self.is_connected = False
        self.need_reconnect = False
        
        # 停止ping线程
        self._stop_ping_thread()
        
        if self.ws:
            try:
                self.ws.close()
            except:
                pass

    def on_error(self, _ws, error):
        """处理WebSocket错误"""
        self.is_connected = False
        
        # 检查是否是需要重连的错误类型
        reconnect_errors = (
            websocket.WebSocketConnectionClosedException,
            websocket.WebSocketBadStatusException,
            websocket.WebSocketTimeoutException,
            socket.error,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            OSError
        )
        
        if isinstance(error, reconnect_errors):
            self.need_reconnect = True
            
            # 更详细的错误信息
            if isinstance(error, websocket.WebSocketConnectionClosedException):
                msg = f"WEBSOCKET API连接被关闭，准备重连..."
            elif isinstance(error, websocket.WebSocketBadStatusException):
                msg = f"WEBSOCKET API收到错误状态码，准备重连..."
            elif isinstance(error, websocket.WebSocketTimeoutException):
                msg = f"WEBSOCKET API连接超时，准备重连..."
            elif isinstance(error, (ConnectionResetError, ConnectionAbortedError)):
                msg = f"WEBSOCKET API连接被远程主机重置，准备重连..."
            else:
                msg = f"WEBSOCKET API网络错误: {error}，准备重连..."
            
            write_log(msg,"HYPERLIQUID")
            
            # 关闭当前连接，触发重连
            if self.ws and self.ws.keep_running:
                try:
                    self.ws.close()
                except:
                    pass
                    
        # DNS解析失败
        elif isinstance(error, socket.gaierror) or (hasattr(error, 'errno') and error.errno == 11001):
            self.need_reconnect = True
            msg = f"WEBSOCKET API DNS解析失败，将重试连接..."
            write_log(msg,"HYPERLIQUID")
            if self.ws and self.ws.keep_running:
                try:
                    self.ws.close()
                except:
                    pass
        else:
            # 其他未知错误
            msg = f"WEBSOCKET API运行出错：{error}"
            write_log(msg,"HYPERLIQUID")
            save_connection_status("HYPERLIQUID", False, msg)

    def on_close(self, _ws, close_status_code=None, close_msg=None):
        """处理WebSocket连接关闭"""
        self.is_connected = False
        self.ws_ready = False
        
        close_info = ""
        if close_status_code:
            close_info += f"状态码: {close_status_code}"
        if close_msg:
            close_info += f" 消息: {close_msg}"
        
        # 正常关闭码
        normal_close_codes = [1000, 1001]
        
        if close_status_code not in normal_close_codes and not self.stop_event.is_set():
            self.need_reconnect = True
            msg = f"WEBSOCKET API连接异常关闭 {close_info}，准备重连...".strip()
        else:
            msg = f"WEBSOCKET API连接已关闭 {close_info}".strip()
        
        write_log(msg,"HYPERLIQUID")
        
        if not self.need_reconnect:
            save_connection_status("HYPERLIQUID", False, msg)

    def on_message(self, _ws, message):
        """处理接收到的消息"""
        if message == "Websocket connection established.":
            return
        ws_msg: WsMsg = json.loads(message)
        identifier = ws_msg_to_identifier(ws_msg)
        if identifier == "pong":
            return
        if identifier is None:
            return
        active_subscriptions = self.active_subscriptions[identifier]
        if len(active_subscriptions) == 0:
            write_log(f"WEBSOCKET API收到意外订阅的Websocket消息：{message}，{identifier}","HYPERLIQUID")
        else:
            for active_subscription in active_subscriptions:
                active_subscription.callback(ws_msg)

    def on_open(self, _ws):
        """处理WebSocket连接打开"""
        write_log(f"WEBSOCKET API SDK连接成功","HYPERLIQUID")

        self.ws_ready = True
        self.is_connected = True
        self.need_reconnect = False
        self.reconnect_attempts = 0  # 重置重连次数
        
        # 启动ping线程
        self._start_ping_thread()
        
        try:
            # 清空active_subscriptions，准备重新订阅
            self.active_subscriptions.clear()
            
            # 恢复所有订阅
            if self.all_subscriptions:
                for subscription, active_subscription in self.all_subscriptions:
                    identifier = subscription_to_identifier(subscription)
                    self.active_subscriptions[identifier].append(active_subscription)
                    self.ws.send(json.dumps({"method": "subscribe", "subscription": subscription}))

            
            # 处理队列中的新订阅（如果有）
            if self.queued_subscriptions:
                for subscription, active_subscription in self.queued_subscriptions:
                    identifier = subscription_to_identifier(subscription)
                    self.active_subscriptions[identifier].append(active_subscription)
                    self.ws.send(json.dumps({"method": "subscribe", "subscription": subscription}))
                    # 添加到所有订阅列表中
                    self.all_subscriptions.append((subscription, active_subscription))
                self.queued_subscriptions.clear()
                        
        except Exception as ex:
            msg = f"WEBSOCKET API重新订阅时出错：{ex}"
            write_log(msg,"HYPERLIQUID")

    def subscribe(
        self, subscription: Subscription, callback: Callable[[Any], None], subscription_id: Optional[int] = None
    ) -> int:
        """订阅WebSocket频道"""
        if subscription_id is None:
            self.subscription_id_counter += 1
            subscription_id = self.subscription_id_counter
            
        active_sub = ActiveSubscription(callback, subscription_id)
        
        if not self.ws_ready or not self.is_connected:
            # 添加到队列，等待连接建立
            self.queued_subscriptions.append((subscription, active_sub))
        else:
            try:
                identifier = subscription_to_identifier(subscription)
                self.active_subscriptions[identifier].append(active_sub)
                self.ws.send(json.dumps({"method": "subscribe", "subscription": subscription}))
                
                # 保存到所有订阅列表（用于重连恢复）
                # 检查是否已存在相同的subscription_id，避免重复
                existing_ids = [sub[1].subscription_id for sub in self.all_subscriptions]
                if subscription_id not in existing_ids:
                    self.all_subscriptions.append((subscription, active_sub))
                    
            except Exception as ex:
                write_log(f"订阅失败: {ex}","HYPERLIQUID")
                # 如果发送失败，添加到队列中等待重连后重新订阅
                self.queued_subscriptions.append((subscription, active_sub))
        return subscription_id

    def unsubscribe(self, subscription: Subscription, subscription_id: int) -> bool:
        """取消订阅WebSocket频道"""
        identifier = subscription_to_identifier(subscription)
        active_subscriptions = self.active_subscriptions[identifier]
        new_active_subscriptions = [x for x in active_subscriptions if x.subscription_id != subscription_id]
        
        # 从队列中移除
        self.queued_subscriptions = [(sub, act_sub) for sub, act_sub in self.queued_subscriptions 
                                        if act_sub.subscription_id != subscription_id]
        
        # 从所有订阅列表中移除
        self.all_subscriptions = [(sub, act_sub) for sub, act_sub in self.all_subscriptions 
                                    if act_sub.subscription_id != subscription_id]
        
        if len(new_active_subscriptions) == 0 and self.is_connected and self.ws_ready:
            try:
                self.ws.send(json.dumps({"method": "unsubscribe", "subscription": subscription}))
            except Exception as e:
                write_log(f"取消订阅失败: {e}","HYPERLIQUID")
                
        self.active_subscriptions[identifier] = new_active_subscriptions
        return len(active_subscriptions) != len(new_active_subscriptions)