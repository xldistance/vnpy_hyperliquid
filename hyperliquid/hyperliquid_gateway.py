import base64
import csv
import hashlib
import hmac
import json
from collections import defaultdict
from copy import copy
from datetime import datetime, timedelta, timezone
from enum import Enum
from inspect import signature
from pathlib import Path
from threading import Lock
from time import sleep, time
from typing import Any, Dict, List
from urllib.parse import urlencode
from hyperliquid.info import Info,Cloid
from hyperliquid.utils import constants
from hyperliquid.exchange import Exchange as HyperliquidExchange
from time import sleep
import eth_account
from eth_account.signers.local import LocalAccount

from peewee import chunked
from vnpy.api.rest import Request, RestClient
from vnpy.api.websocket import WebsocketClient
from vnpy.event import Event
from vnpy.event.engine import EventEngine
from vnpy.trader.constant import Direction, Exchange, Interval, Offset, Status
from vnpy.trader.database import database_manager
from vnpy.trader.event import EVENT_TIMER
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    AccountData,
    BarData,
    CancelRequest,
    ContractData,
    HistoryRequest,
    OrderData,
    OrderRequest,
    OrderType,
    PositionData,
    Product,
    SubscribeRequest,
    TickData,
    TradeData,
)
from vnpy.trader.setting import hyperliquid_account  # 导入账户字典
from vnpy.trader.utility import (
    TZ_INFO,
    GetFilePath,
    delete_dr_data,
    extract_vt_symbol,
    get_symbol_mark,
    get_local_datetime,
    get_uuid,
    is_target_contract,
    load_json,
    remain_alpha,
    remain_digit,
    save_connection_status,
    save_json,
)

recording_list = GetFilePath.recording_list

# REST API地址
REST_HOST: str = "https://api.hyperliquid.xyz"

# Websocket API地址
WEBSOCKET_HOST: str = "wss://api.hyperliquid.xyz/ws"

# 买卖方向映射
DIRECTION_VT2HYPERLIQUID = {
    Direction.LONG: "B",
    Direction.SHORT: "A",
}
DIRECTION_HYPERLIQUID2VT = {v: k for k, v in DIRECTION_VT2HYPERLIQUID.items()}
TRADE_DIRECTION_HYPERLIQUID2VT= {
    "Open Long":(Direction.LONG,Offset.OPEN),
    "Close Long":(Direction.SHORT,Offset.CLOSE),
    "Open Short":(Direction.SHORT,Offset.OPEN),
    "Close Short":(Direction.LONG,Offset.CLOSE),
    "Buy":(Direction.LONG,Offset.NONE),
    "Sell":(Direction.SHORT,Offset.NONE),
}
STATUS_MAP = {
            "open":Status.NOTTRADED,
            "filled":Status.ALLTRADED,
            "canceled":Status.CANCELLED
        }
# 多空反向映射
OPPOSITE_DIRECTION = {
    Direction.LONG: Direction.SHORT,
    Direction.SHORT: Direction.LONG,
}


# 鉴权类型
class Security(Enum):
    NONE: int = 0
    SIGNED: int = 1
SPOT_INDEX_NAME_MAP = {}
# ----------------------------------------------------------------------------------------------------
class HyperliquidGateway(BaseGateway):
    """vn.py用于对接HYPERLIQUID的交易接口"""

    default_setting: Dict[str, Any] = {
        "key": "",
        "secret": "",
        "host": "",
        "port": 0,
    }

    exchanges: List[Exchange] = [Exchange.HYPE,Exchange.HYPESPOT]
    # ----------------------------------------------------------------------------------------------------
    def __init__(self, event_engine: EventEngine, gateway_name: str = "HYPERLIQUID") -> None:
        """
        构造函数
        """
        super().__init__(event_engine, gateway_name)

        self.ws_api: "HyperliquidWebsocketApi" = HyperliquidWebsocketApi(self)
        self.rest_api: "HyperliquidRestApi" = HyperliquidRestApi(self)
        self.orders: Dict[str, OrderData] = {}
        self.recording_list = [vt_symbol for vt_symbol in recording_list if is_target_contract(vt_symbol, self.gateway_name)]
        # 查询历史数据合约列表
        self.history_contracts = copy(self.recording_list)
        self.query_functions = [self.query_order]
        # 查询历史数据状态
        self.history_status = True
        # 订阅逐笔成交数据状态
        self.book_trade_status: bool = False
    # ----------------------------------------------------------------------------------------------------
    def connect(self, log_account: dict = {}) -> None:
        """
        连接交易接口
        """
        if not log_account:
            log_account = hyperliquid_account
        account_address: str = log_account["account_address"]
        eth_private_address: str = log_account["eth_private_address"]
        proxy_host: str = log_account["host"]
        proxy_port: str = log_account["port"]
        self.account_file_name = log_account["account_file_name"]
        account: LocalAccount = eth_account.Account.from_key(eth_private_address)
        self.exchange_info = HyperliquidExchange(account, REST_HOST, account_address=account.address, perp_dexs=None)
        self.rest_api.connect(account_address,eth_private_address,proxy_host,proxy_port)
        self.ws_api.connect(account_address,eth_private_address,proxy_host,proxy_port)
        self.init_query()
    # ----------------------------------------------------------------------------------------------------
    def subscribe(self, req: SubscribeRequest) -> None:
        """
        订阅行情
        """
        self.ws_api.subscribe(req)
    # ----------------------------------------------------------------------------------------------------
    def send_order(self, req: OrderRequest) -> str:
        """
        委托下单
        """
        return self.rest_api.send_order(req)
    # ----------------------------------------------------------------------------------------------------
    def cancel_order(self, req: CancelRequest) -> None:
        """
        委托撤单
        """
        self.rest_api.cancel_order(req)
    # ----------------------------------------------------------------------------------------------------
    def query_account(self) -> None:
        """
        查询资金
        """
        self.rest_api.query_account()
    # ----------------------------------------------------------------------------------------------------
    def query_position(self) -> None:
        """
        查询持仓
        """
        self.rest_api.query_position()
    # ----------------------------------------------------------------------------------------------------
    def query_order(self) -> None:
        """
        查询活动委托单
        """
        self.rest_api.query_order()
    # ----------------------------------------------------------------------------------------------------
    def on_order(self, order: OrderData) -> None:
        """
        推送委托数据
        """
        self.orders[order.orderid] = copy(order)
        super().on_order(order)
    # ----------------------------------------------------------------------------------------------------
    def get_order(self, orderid: str) -> OrderData:
        """
        查询委托数据
        """
        return self.orders.get(orderid, None)
    # ----------------------------------------------------------------------------------------------------
    def query_history(self, event: Event):
        """
        查询合约历史数据
        """
        if len(self.history_contracts) > 0:
            symbol, exchange, gateway_name = extract_vt_symbol(self.history_contracts.pop(0))
            req = HistoryRequest(
                symbol=symbol,
                exchange=exchange,
                interval=Interval.MINUTE,
                start=datetime.now(TZ_INFO) - timedelta(minutes=1440),
                end=datetime.now(TZ_INFO),
                gateway_name=self.gateway_name,
            )
            self.rest_api.query_history(req)
            self.rest_api.set_leverage(symbol,exchange)
    # ----------------------------------------------------------------------------------------------------
    def process_timer_event(self, event) -> None:
        """
        处理定时事件
        """
        # 每秒查询一次账户资金
        self.query_account()
        function = self.query_functions.pop(0)
        function()
        self.query_functions.append(function)
    # ----------------------------------------------------------------------------------------------------
    def init_query(self):
        """ """
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)
        if self.history_status:
            self.event_engine.register(EVENT_TIMER, self.query_history)
    # ----------------------------------------------------------------------------------------------------
    def close(self) -> None:
        """
        关闭连接
        """
        self.rest_api.stop()
        self.ws_api.stop()
        self.ws_api.ws_info.disconnect_websocket()
# ----------------------------------------------------------------------------------------------------
class HyperliquidRestApi(RestClient):
    """
    HYPERLIQUID交易所REST API
    """
    # ----------------------------------------------------------------------------------------------------
    def __init__(self, gateway: HyperliquidGateway) -> None:
        """
        构造函数
        """
        super().__init__()

        self.gateway = gateway
        self.gateway_name: str = gateway.gateway_name

        # 保存用户登陆信息
        self.account_address: str = ""
        self.eth_private_address: str = ""
        # 生成委托单号加线程锁
        self.order_count: int = 0
        self.order_count_lock: Lock = Lock()
        self.connect_time: int = 0
        self.account_date = None  # 账户日期
        self.accounts_info: Dict[str, dict] = {}
        self.system_local_orderid_map = {}
        self.spot_inited = False # 现货信息查询状态
        self.spot_symbol_name_map = {}
        self.spot_name_symbol_map = {}
    # ----------------------------------------------------------------------------------------------------
    def sign(self, request: Request) -> Request:
        """
        生成HYPERLIQUID签名
        """
        # 获取鉴权类型并将其从data中删除
        security = request.data.pop("security")
        if security == Security.NONE:
            request.data = None
            return request

        return request
    # ----------------------------------------------------------------------------------------------------
    def connect(
        self,
        account_address: str,
        eth_private_address: str,
        proxy_host: str,
        proxy_port: int,
    ) -> None:
        """
        连接REST服务器
        """
        self.account_address = account_address
        self.eth_private_address = eth_private_address
        self.connect_time = int(datetime.now().strftime("%Y%m%d%H%M%S"))
        self.init(REST_HOST, proxy_host, proxy_port, gateway_name=self.gateway_name)
        self.rest_info = Info(REST_HOST, skip_ws=True)
        self.start()
        self.gateway.write_log(f"交易接口：{self.gateway_name}，REST API启动成功")
        self.query_contract()
    # ----------------------------------------------------------------------------------------------------
    def query_account(self) -> None:
        """
        查询资金
        """
        data = self.rest_info.user_state(self.account_address)
        self.on_query_account(data)
        spot_data = self.rest_info.spot_user_state(self.account_address)
        self.on_query_spot_account(spot_data)
    # ----------------------------------------------------------------------------------------------------
    def query_order(self) -> None:
        """
        查询活动委托单
        """
        data = self.rest_info.open_orders(self.gateway.exchange_info.wallet.address)
        self.on_query_order(data)
    # ----------------------------------------------------------------------------------------------------
    def query_contract(self) -> None:
        """
        查询合约信息
        """
        perp_data = self.rest_info.meta()
        self.on_query_perp_contract(perp_data)
        spot_data = self.rest_info.spot_meta()
        self.on_query_spot_contract(spot_data)
    # ----------------------------------------------------------------------------------------------------
    def set_leverage(self, symbol: str,exchange:Exchange) -> None:
        """
        设置全仓合约杠杆
        """
        # 现货不设置杠杆
        if exchange == Exchange.HYPESPOT:
            return
        leverage_result = self.gateway.exchange_info.update_leverage(10,symbol)
        self.on_leverage(leverage_result)
    # ----------------------------------------------------------------------------------------------------
    def on_leverage(self,data:dict):
        """
        收到设置杠杆数据回调
        """
        pass
    # ----------------------------------------------------------------------------------------------------
    def _new_order_id(self) -> int:
        """
        生成本地委托号
        """
        with self.order_count_lock:
            self.order_count += 1
            return self.order_count
    # ----------------------------------------------------------------------------------------------------
    def send_order(self, req: OrderRequest) -> str:
        """
        委托下单
        """
        # 生成本地委托号
        new_order_id = str(self._new_order_id()).rjust(18, '0')
        orderid: str = "0x" + str(self.connect_time) + new_order_id

        # 推送提交中事件
        order: OrderData = req.create_order_data(orderid, self.gateway_name)
        self.gateway.on_order(order)
        is_buy = True if order.direction == Direction.LONG else False
        # 现货不支持reduce_only
        reduce_only = req.offset == Offset.CLOSE and req.exchange == Exchange.HYPE
        if req.exchange == Exchange.HYPESPOT:
            symbol = self.spot_symbol_name_map[req.symbol]
        else:
            symbol = req.symbol
        data = self.gateway.exchange_info.order(symbol, is_buy, req.volume, req.price, {"limit": {"tif": "Gtc"}},reduce_only,cloid=Cloid(orderid))
        self.on_send_order(data,order)
        return order.vt_orderid
    # ----------------------------------------------------------------------------------------------------
    def cancel_order(self, req: CancelRequest) -> None:
        """
        委托撤单
        """
        while not self.spot_symbol_name_map:
            sleep(1)
        if req.exchange == Exchange.HYPESPOT:
            symbol = self.spot_symbol_name_map[req.symbol]
        else:
            symbol = req.symbol
        # 自定义委托单id撤单
        if isinstance(req.orderid,str):
            data = self.gateway.exchange_info.cancel_by_cloid(symbol,Cloid(req.orderid))
        else:
            # 系统委托单id撤单
            data = self.gateway.exchange_info.cancel(symbol,req.orderid)
        self.on_cancel_order(data,req)
    # ----------------------------------------------------------------------------------------------------
    def on_query_spot_account(self,data:dict):
        """
        现货资金回报
        """
        if not data:
            return
        data =data["balances"]
        if len(data) == 1:
            for symbol_exchange in self.gateway.ws_api.ticks:
                symbol,exchange = symbol_exchange.split("_")
                # 过滤期货合约
                if exchange == "HYPE":
                    continue
                long_position = PositionData(
                    symbol=symbol,
                    exchange=Exchange.HYPESPOT,
                    direction=Direction.LONG,
                    volume=0,
                    price=0,
                    pnl=0,
                    frozen=0,
                    gateway_name=self.gateway_name,
                )
                short_position = PositionData(
                    symbol=symbol,
                    exchange=Exchange.HYPESPOT,
                    direction=Direction.SHORT,
                    volume=0,
                    price=0,
                    pnl=0,
                    frozen=0,
                    gateway_name=self.gateway_name,
                )
                self.gateway.on_position(long_position)
                self.gateway.on_position(short_position)
        else:
            for raw in data:
                symbol = raw["coin"]
                if symbol == "USDC":
                    continue
                long_position = PositionData(
                    symbol = symbol,
                    exchange= Exchange.HYPESPOT,
                    gateway_name=self.gateway_name,
                    volume=float(raw["total"]),
                    direction=Direction.LONG,
                    frozen=float(raw["hold"]),
                )
                short_position = PositionData(
                    symbol=symbol,
                    exchange=Exchange.HYPESPOT,
                    direction=Direction.SHORT,
                    volume=0,
                    price=0,
                    pnl=0,
                    frozen=0,
                    gateway_name=self.gateway_name,
                )
                self.gateway.on_position(long_position)
                self.gateway.on_position(short_position)

        for raw in data:
            account: AccountData = AccountData(
                accountid=f"{raw['coin']}_SPOT_{self.gateway_name}",
                balance=float(raw["total"]),
                datetime=datetime.now(TZ_INFO),
                file_name=self.gateway.account_file_name,
                gateway_name=self.gateway_name,
            )
            account.available = account.balance - float(raw["hold"])
            account.frozen = account.balance - account.available
            if account.balance:
                self.gateway.on_account(account)
                # 保存账户资金信息
                self.accounts_info[account.accountid] = account.__dict__

        if not self.accounts_info:
            return
        accounts_info = list(self.accounts_info.values())
        account_date = accounts_info[-1]["datetime"].date()
        account_path = str(GetFilePath.ctp_account_path).replace("ctp_account_main", self.gateway.account_file_name)
        write_header = not Path(account_path).exists()
        additional_writing = self.account_date and self.account_date != account_date
        self.account_date = account_date
        # 文件不存在则写入文件头，否则只在日期变更后追加写入文件
        if not write_header and not additional_writing:
            return
        write_mode = "w" if write_header else "a"
        for account_data in accounts_info:
            with open(account_path, write_mode, newline="") as f1:
                w1 = csv.DictWriter(f1, list(account_data))
                if write_header:
                    w1.writeheader()
                w1.writerow(account_data)
    # ----------------------------------------------------------------------------------------------------
    def on_query_account(self, data: dict) -> None:
        """
        合约资金查询回报
        """
        self.on_query_position(data["assetPositions"])

        account: AccountData = AccountData(
            accountid="USDC" + "_" + self.gateway_name,
            balance=float(data["crossMarginSummary"]["totalRawUsd"]),
            available=float(data["withdrawable"]),
            datetime=get_local_datetime(data["time"]),
            file_name=self.gateway.account_file_name,
            gateway_name=self.gateway_name,
        )
        account.frozen = account.balance - account.available
        if account.balance:
            self.gateway.on_account(account)
            # 保存账户资金信息
            self.accounts_info[account.accountid] = account.__dict__

        if not self.accounts_info:
            return
        accounts_info = list(self.accounts_info.values())
        account_date = accounts_info[-1]["datetime"].date()
        account_path = str(GetFilePath.ctp_account_path).replace("ctp_account_main", self.gateway.account_file_name)
        write_header = not Path(account_path).exists()
        additional_writing = self.account_date and self.account_date != account_date
        self.account_date = account_date
        # 文件不存在则写入文件头，否则只在日期变更后追加写入文件
        if not write_header and not additional_writing:
            return
        write_mode = "w" if write_header else "a"
        for account_data in accounts_info:
            with open(account_path, write_mode, newline="") as f1:
                w1 = csv.DictWriter(f1, list(account_data))
                if write_header:
                    w1.writeheader()
                w1.writerow(account_data)
    # ----------------------------------------------------------------------------------------------------
    def on_query_position(self, data: dict | list) -> None:
        """
        持仓查询回报
        """
        if not data:
            for symbol_exchange in self.gateway.ws_api.ticks:
                symbol,exchange = symbol_exchange.split("_")
                # 过滤现货合约
                if exchange == "HYPESPOT":
                    continue
                long_position = PositionData(
                    symbol=symbol,
                    exchange=Exchange.HYPE,
                    direction=Direction.LONG,
                    volume=0,
                    price=0,
                    pnl=0,
                    frozen=0,
                    gateway_name=self.gateway_name,
                )
                short_position = PositionData(
                    symbol=symbol,
                    exchange=Exchange.HYPE,
                    direction=Direction.SHORT,
                    volume=0,
                    price=0,
                    pnl=0,
                    frozen=0,
                    gateway_name=self.gateway_name,
                )
                self.gateway.on_position(long_position)
                self.gateway.on_position(short_position)
            return
        for raw in data:
            raw = raw["position"]
            volume = float(raw["szi"])
            if volume >= 0:
                direction = Direction.LONG
            elif volume < 0:
                direction = Direction.SHORT
            position_1: PositionData = PositionData(
                symbol=raw["coin"],
                exchange=Exchange.HYPE,
                direction=direction,
                volume=abs(volume),
                price=float(raw["entryPx"]),
                pnl=float(raw["unrealizedPnl"]),
                gateway_name=self.gateway_name,
            )
            position_2 = PositionData(
                symbol=raw["coin"],
                exchange=Exchange.HYPE,
                gateway_name=self.gateway_name,
                direction=OPPOSITE_DIRECTION[position_1.direction],
                volume=0,
                price=0,
                pnl=0,
            )
            self.gateway.on_position(position_1)
            self.gateway.on_position(position_2)
    # ----------------------------------------------------------------------------------------------------
    def query_position(self):
        pass
    # ----------------------------------------------------------------------------------------------------
    def on_query_order(self, data: dict) -> None:
        """
        委托查询回报
        """
        for raw in data:
            symbol = raw["coin"]
            if symbol.startswith("@"):
                symbol = self.spot_name_symbol_map[symbol]
                exchange = Exchange.HYPESPOT
            else:
                exchange = Exchange.HYPE
            volume = float(raw["origSz"])
            untrade_volume = float(raw["sz"])
            trade_volume = volume - untrade_volume
            if volume == untrade_volume:
                status = Status.NOTTRADED
            elif volume > untrade_volume:
                status = Status.PARTTRADED
            if "cloid" not in raw:
                orderid = self.system_local_orderid_map.get(raw["oid"],raw["oid"])
            else:
                orderid = raw["cloid"]
                self.system_local_orderid_map[raw["oid"]] = orderid
            order: OrderData = OrderData(
                orderid=orderid,
                symbol=symbol,
                exchange=exchange,
                price=float(raw["limitPx"]),
                volume=volume,
                direction=DIRECTION_HYPERLIQUID2VT[raw["side"]],
                traded=trade_volume,
                status=status,
                datetime=get_local_datetime(raw["timestamp"]),
                gateway_name=self.gateway_name,
            )
            if "reduceOnly" in raw and raw["reduceOnly"]:
                order.offset = Offset.CLOSE
            self.gateway.on_order(order)
    # ----------------------------------------------------------------------------------------------------
    def on_query_spot_contract(self,data:dict):
        """
        现货信息数据
        """
        # universe中tokens列表第一个值是tokens中的index，交易所下单需要使用universe中的name
        for raw in data["universe"]:
            SPOT_INDEX_NAME_MAP[raw["tokens"][0]] = raw["name"]
            #{'tokens': [299, 0], 'name': '@188', 'index': 188, 'isCanonical': False}
        for raw in data["tokens"]:
            # 过滤非evm合约
            if not raw["evmContract"]:
                continue
            #{'name': 'UPUMP', 'szDecimals': 0, 'weiDecimals': 6, 'index': 299, 'tokenId': '0x544e60f98a36d7b22c0fb5824b84f795', 'isCanonical': False, 'evmContract': {'address': '0x27ec642013bcb3d80ca3706599d3cda04f6f4452', 'evm_extra_wei_decimals': 0}, 'fullName': 'Unit Pump Fun', 'deployerTradingFeeShare': '1.0'}
            symbol:str = raw["name"]
            # 现货索引和名称映射
            name = SPOT_INDEX_NAME_MAP.get(raw["index"])
            if not name:
                continue
            self.spot_symbol_name_map[symbol] = name
            max_decimal = 8
            volume_decimal = raw["szDecimals"]
            min_volume = 10 ** (-volume_decimal)
            price_decimal = min(5,max_decimal - volume_decimal)
            price_tick = 10 ** (-price_decimal)

            contract: ContractData = ContractData(
                symbol=symbol,
                exchange=Exchange.HYPESPOT,
                name=name,
                price_tick=price_tick,
                min_volume=min_volume,
                size=10,
                product=Product.SPOT,
                gateway_name=self.gateway_name,
            )
            self.gateway.on_contract(contract)
        self.spot_name_symbol_map = {v:k for k,v in self.spot_symbol_name_map.items()}
        self.spot_inited = True
        self.gateway.write_log(f"交易接口：{self.gateway_name}，现货信息查询成功")
    # ----------------------------------------------------------------------------------------------------
    def on_query_perp_contract(self, data: dict):
        """
        合约信息查询回报
        """
        for raw in data["universe"]:
            symbol:str = raw["name"]
            max_decimal = 6
            volume_decimal = raw["szDecimals"]
            min_volume = 10 ** (-volume_decimal)
            price_decimal = min(5,max_decimal - volume_decimal)
            price_tick = 10 ** (-price_decimal)

            contract: ContractData = ContractData(
                symbol=symbol,
                exchange=Exchange.HYPE,
                name=raw["name"],
                price_tick=price_tick,
                min_volume=min_volume,
                size=10,
                product=Product.FUTURES,
                gateway_name=self.gateway_name,
            )
            self.gateway.on_contract(contract)
        self.gateway.write_log(f"交易接口：{self.gateway_name}，合约信息查询成功")
    # ----------------------------------------------------------------------------------------------------
    def on_send_order(self, data: dict,order:OrderData) -> None:
        """
        委托下单回报
        """
        response = data["response"]["data"]["statuses"][0]
        if "error" in response:
            msg = response["error"]
            order.status = Status.REJECTED
            self.gateway.on_order(order)
            self.gateway.write_log(f"合约：{order.vt_symbol}委托失败，信息：{msg}")
        else:
            if "filled" in response:
                self.system_local_orderid_map[response["filled"]["oid"]] = order.orderid
            else:
                self.system_local_orderid_map[response["resting"]["oid"]] = order.orderid
    # ----------------------------------------------------------------------------------------------------
    def on_cancel_order(self, data:dict,req:CancelRequest) -> None:
        """
        委托撤单回报
        """
        status = data["response"]["data"]["statuses"][0]
        if 'error' in status:
            msg = status["error"]
            order = self.gateway.orders[req.orderid]
            order.status = Status.CANCELLED
            self.gateway.on_order(order)
            self.gateway.write_log(f"合约：{req.symbol}撤单失败，错误信息：{msg}")
    # ----------------------------------------------------------------------------------------------------
    def query_history(self, req: HistoryRequest) -> List[BarData]:
        """
        查询历史数据
        """
        history = []
        limit = 200
        start_time = req.start
        time_consuming_start = time()
        if req.exchange == Exchange.HYPESPOT:
            symbol = self.spot_symbol_name_map[req.symbol]
        else:
            symbol = req.symbol
        # 已经获取了所有可用的历史数据或者start已经到了请求的终止时间则终止循环
        while start_time < req.end:
            end_time = start_time + timedelta(minutes=limit)
            candle = self.rest_info.candles_snapshot(symbol,"1m",int(start_time.timestamp()*1000),int(end_time.timestamp()*1000))
            buf = []
            for raw_data in candle:
                volume = float(raw_data["v"])
                bar = BarData(
                    symbol=req.symbol,
                    exchange=req.exchange,
                    datetime=get_local_datetime(raw_data["t"]),
                    interval=req.interval,
                    open_price=raw_data["o"],
                    high_price=raw_data["h"],
                    low_price=raw_data["l"],
                    close_price=raw_data["c"],
                    volume=volume,
                    gateway_name=self.gateway_name,
                )
                buf.append(bar)
            history.extend(buf)
            start_time = end_time + timedelta(minutes=1)

        if history:
            try:
                database_manager.save_bar_data(history, False)
            except Exception as err:
                self.gateway.write_log(f"{err}")
                return

            time_consuming_end = time()
            query_time = round(time_consuming_end - time_consuming_start, 3)
            msg = f"载入{req.vt_symbol}:bar数据，开始时间：{history[0].datetime}，结束时间：{history[-1].datetime}，数据量：{len(history)}，耗时:{query_time}秒"
            self.gateway.write_log(msg)
        else:
            msg = f"未获取到合约：{req.vt_symbol}历史数据"
            self.gateway.write_log(msg)
# ----------------------------------------------------------------------------------------------------
class HyperliquidWebsocketApi(WebsocketClient):
    """
    HYPERLIQUID交易所Websocket接口
    """
    # ----------------------------------------------------------------------------------------------------
    def __init__(self, gateway: HyperliquidGateway) -> None:
        """
        构造函数
        """
        super().__init__()

        self.gateway: HyperliquidGateway = gateway
        self.gateway_name: str = gateway.gateway_name
        self.ticks: Dict[str, TickData] = {}
        self.subscribed: Dict[str, SubscribeRequest] = {}
        # 成交委托号
        self.trade_id: int = 0
        self.ws_connected: bool = False
        self.ping_count = 0
        self.trade_ids = [] # trade_id过滤
    # ----------------------------------------------------------------------------------------------------
    def connect(self, account_address: str, eth_private_address: str, proxy_host: str, proxy_port: int) -> None:
        """
        连接Websocket交易频道
        """
        self.account_address = account_address
        self.eth_private_address = eth_private_address
        self.ws_info = Info(REST_HOST, skip_ws=False)
        self.init(WEBSOCKET_HOST, proxy_host, proxy_port, gateway_name=self.gateway_name)
        self.start()
        self.gateway.event_engine.register(EVENT_TIMER, self.send_ping)
    # ----------------------------------------------------------------------------------------------------
    def send_ping(self, event):
        """
        发送ping
        """
        self.ping_count += 1
        if self.ping_count < 10:
            return
        self.ping_count = 0
        self.send_packet({ "method": "ping" })
    # ----------------------------------------------------------------------------------------------------
    def on_connected(self) -> None:
        """
        连接成功回报
        """
        self.ws_connected = True
        self.gateway.write_log(f"交易接口：{self.gateway_name}，Websocket API连接成功")

        for req in list(self.subscribed.values()):
            self.subscribe(req)
    # ----------------------------------------------------------------------------------------------------
    def on_disconnected(self) -> None:
        """
        连接断开回报
        """
        self.ws_connected = False
        self.gateway.write_log(f"交易接口：{self.gateway_name}，Websocket API连接断开")
    # ----------------------------------------------------------------------------------------------------
    def subscribe(self, req: SubscribeRequest) -> None:
        """
        订阅行情
        """
        # 等待ws连接成功和现货信息查询完成后再订阅行情
        while (not self.ws_connected or not self.gateway.rest_api.spot_inited):
            sleep(1)
        symbol_exchange = f"{req.symbol}_{req.exchange.value}"
        self.ticks[symbol_exchange] = TickData(
            symbol=req.symbol,
            name=req.symbol,
            exchange=req.exchange,
            gateway_name=self.gateway_name,
            datetime=datetime.now(TZ_INFO),
        )
        self.subscribed[symbol_exchange] = req
        if req.exchange == Exchange.HYPESPOT:
            subscribe_symbol = self.gateway.rest_api.spot_symbol_name_map[req.symbol]
        else:
            subscribe_symbol = req.symbol
        self.ws_info.subscribe({'type': 'l2Book', 'coin': subscribe_symbol}, self.on_depth)
        self.ws_info.subscribe({ "type": "trades", "coin": subscribe_symbol}, self.on_public_trade)
        self.ws_info.subscribe({"type": "userFills", "user": self.gateway.exchange_info.wallet.address}, self.on_trade)
        self.ws_info.subscribe({"type": "orderUpdates", "user": self.gateway.exchange_info.wallet.address}, self.on_order)
        if self.gateway.book_trade_status:
            self.ws_info.subscribe({"type": "bbo", "coin": subscribe_symbol}, self.on_bbo)
    # ----------------------------------------------------------------------------------------------------
    def on_packet(self, packet: Any) -> None:
        """
        推送数据回报
        """
        pass
    # ----------------------------------------------------------------------------------------------------
    def on_bbo(self, packet: dict):
        """
        收到逐笔一档委托簿推送
        """
        data = packet["data"]
        bbo = data["bbo"]
        symbol = data["coin"]
        if symbol.startswith("@"):
            symbol = self.gateway.rest_api.spot_name_symbol_map[symbol]
            exchange = Exchange.HYPESPOT
        else:
            exchange = Exchange.HYPE
        bid_price_1,bid_volume_1 = float(bbo[0]["px"]),float(bbo[0]["sz"])
        ask_price_1,ask_volume_1 = float(bbo[1]["px"]),float(bbo[1]["sz"])
        tick = self.ticks[f"{symbol}_{exchange.value}"]
        tick.datetime = get_local_datetime(data["time"])
        tick.bid_price_1,tick.bid_volume_1 = bid_price_1,bid_volume_1
        tick.ask_price_1,tick.ask_volume_1 = ask_price_1,ask_volume_1
        self.gateway.on_tick(tick)
    # ----------------------------------------------------------------------------------------------------
    def on_public_trade(self,packet:dict):
        data = packet["data"]
        for data in packet["data"]:
            symbol = data["coin"]
            if symbol.startswith("@"):
                symbol = self.gateway.rest_api.spot_name_symbol_map[symbol]
                exchange = Exchange.HYPESPOT
            else:
                exchange = Exchange.HYPE
            tick = self.ticks[f"{symbol}_{exchange.value}"]
            tick.datetime = get_local_datetime(data["time"])
            tick.last_price = float(data["px"])
    # ----------------------------------------------------------------------------------------------------
    def on_depth(self, packet: dict):
        """
        收到orderbook事件回报
        """
        data = packet["data"]
        symbol:str = data["coin"]
        if symbol.startswith("@"):
            symbol = self.gateway.rest_api.spot_name_symbol_map[symbol]
            exchange = Exchange.HYPESPOT
        else:
            exchange = Exchange.HYPE
        tick = self.ticks[f"{symbol}_{exchange.value}"]
        order_book = data["levels"]
        bids,asks = order_book[0],order_book[1]
        # 封装更新order book的逻辑到一个辅助函数
        def update_order_book(order_books, type_prefix):
            for index, data in enumerate(order_books, start=1):
                setattr(tick, f"{type_prefix}_price_{index}", float(data["px"]))
                setattr(tick, f"{type_prefix}_volume_{index}", float(data["sz"]))

        # 更新买单和卖单的order book
        update_order_book(bids, "bid")
        update_order_book(asks, "ask")

        tick.datetime = get_local_datetime(data["time"])
        if tick.last_price:
            self.gateway.on_tick(tick)
    # ----------------------------------------------------------------------------------------------------
    def on_trade(self,packet:dict):
        """
        收到成交回报
        """
        data = packet["data"]["fills"]
        for raw in data:
            trade_id = raw["tid"]
            # 过滤重复trade_id
            if trade_id  in self.trade_ids:
                continue
            self.trade_ids.append(trade_id)
            if "cloid" in raw:
                orderid = raw["cloid"]
                self.gateway.rest_api.system_local_orderid_map[raw["oid"]] = orderid
            else:
                orderid = raw["oid"]
            symbol = raw["coin"]
            if symbol.startswith("@"):
                symbol = self.gateway.rest_api.spot_name_symbol_map[symbol]
                exchange = Exchange.HYPESPOT
            else:
                exchange = Exchange.HYPE
            direction,offset = TRADE_DIRECTION_HYPERLIQUID2VT[raw["dir"]]
            trade_data = TradeData(
                symbol = symbol,
                exchange= exchange,
                gateway_name=self.gateway_name,
                price = float(raw["px"]),
                volume = float(raw["sz"]),
                direction = direction,
                offset = offset,
                orderid=orderid,
                tradeid=trade_id,
                datetime = get_local_datetime(raw["time"]),
            )
            self.gateway.on_trade(trade_data)
    # ----------------------------------------------------------------------------------------------------
    def on_order(self, packet: dict):
        """
        收到委托事件回报
        """
        data = packet["data"]
        for raw_data in data:
            raw =raw_data["order"]
            symbol=raw["coin"]
            if symbol.startswith("@"):
                symbol = self.gateway.rest_api.spot_name_symbol_map[symbol]
                exchange = Exchange.HYPESPOT
            else:
                exchange = Exchange.HYPE
            volume = float(raw["origSz"])
            remain = float(raw["sz"])
            trade_volume = volume -remain
            if "cloid" not in raw:
                orderid = self.gateway.rest_api.system_local_orderid_map.get(raw["oid"],raw["oid"])
            else:
                orderid = raw["cloid"]
                self.gateway.rest_api.system_local_orderid_map[raw["oid"]] = orderid

            order: OrderData = OrderData(
                orderid=orderid,
                symbol=symbol,
                exchange=exchange,
                price=float(raw["limitPx"]),
                volume=volume,
                direction=DIRECTION_HYPERLIQUID2VT[raw["side"]],
                traded=trade_volume,
                status=STATUS_MAP[raw_data["status"]],
                datetime=get_local_datetime(raw["timestamp"]),
                gateway_name=self.gateway_name,
            )
            # 添加部分成交委托状态
            if order.status == Status.ALLTRADED and remain:
                order.status = Status.PARTTRADED
            if "reduceOnly" in raw and raw["reduceOnly"]:
                order.offset = Offset.CLOSE
            self.gateway.on_order(order)

