from hyperliquid.api import API
from hyperliquid.utils.types import (
    Any,
    Callable,
    Cloid,
    List,
    Meta,
    Optional,
    SpotMeta,
    SpotMetaAndAssetCtxs,
    Subscription,
    cast,
)
from hyperliquid.websocket_manager import WebsocketManager
from vnpy.trader.utility import save_connection_status, write_log

class Info(API):
    def __init__(
        self,
        base_url: Optional[str] = None,
        skip_ws: Optional[bool] = False,
        meta: Optional[Meta] = None,
        spot_meta: Optional[SpotMeta] = None,
        # 注意：当 perp_dexs 为 None 时，将使用 "" 作为永续合约交易所。"" 代表原始交易所。
        perp_dexs: Optional[List[str]] = None,
        timeout: Optional[float] = None,
    ):  # pylint: disable=too-many-locals
        super().__init__(base_url, timeout)
        self.ws_manager: Optional[WebsocketManager] = None
        if not skip_ws:
            self.ws_manager = WebsocketManager(self.base_url)
            self.ws_manager.start()

        if spot_meta is None:
            spot_meta = self.spot_meta()

        if not spot_meta:
            return
        self.coin_to_asset = {}
        self.name_to_coin = {}
        self.asset_to_sz_decimals = {}
        if "universe" not in spot_meta:
            return

        # 现货资产从 10000 开始
        for spot_info in spot_meta["universe"]:
            asset = spot_info["index"] + 10000
            self.coin_to_asset[spot_info["name"]] = asset
            self.name_to_coin[spot_info["name"]] = spot_info["name"]
            base, quote = spot_info["tokens"]
            base_info = spot_meta["tokens"][base]
            quote_info = spot_meta["tokens"][quote]
            self.asset_to_sz_decimals[asset] = base_info["szDecimals"]
            name = f'{base_info["name"]}/{quote_info["name"]}'
            if name not in self.name_to_coin:
                self.name_to_coin[name] = spot_info["name"]

        perp_dex_to_offset = {"": 0}
        if perp_dexs is None:
            perp_dexs = [""]
        else:
            try:
                for i, perp_dex in enumerate(self.perp_dexs()[1:]):
                    # 构建者部署的永续合约交易所从 110000 开始
                    perp_dex_to_offset[perp_dex["name"]] = 110000 + i * 10000
            except Exception as err:
                msg = f"Info接口运行出错，错误信息：{err}"
                write_log(msg,"HYPERLIQUID")
                save_connection_status("HYPERLIQUID", False, msg)
        for perp_dex in perp_dexs:
            offset = perp_dex_to_offset[perp_dex]
            if perp_dex == "" and meta is not None:
                self.set_perp_meta(meta, 0)
            else:
                fresh_meta = self.meta(dex=perp_dex)
                self.set_perp_meta(fresh_meta, offset)

    def set_perp_meta(self, meta: Meta, offset: int) -> Any:
        if "universe" not in meta:
            return
        for asset, asset_info in enumerate(meta["universe"]):
            asset += offset
            self.coin_to_asset[asset_info["name"]] = asset
            self.name_to_coin[asset_info["name"]] = asset_info["name"]
            self.asset_to_sz_decimals[asset] = asset_info["szDecimals"]

    def disconnect_websocket(self):
        if self.ws_manager is None:
            raise RuntimeError("无法调用 disconnect_websocket，因为使用了 skip_ws")
        else:
            self.ws_manager.stop()

    def user_state(self, address: str, dex: str = "") -> Any:
        """获取用户的交易详情。

        POST /info

        参数：
            address (str): 链上地址，42个字符的十六进制格式；
                            例如 0x0000000000000000000000000000000000000000。
        返回：
            {
                assetPositions: [
                    {
                        position: {
                            coin: str,
                            entryPx: Optional[float string]
                            leverage: {
                                type: "cross" | "isolated",
                                value: int,
                                rawUsd: float string  # 仅当类型为 "isolated" 时
                            },
                            liquidationPx: Optional[float string]
                            marginUsed: float string,
                            positionValue: float string,
                            returnOnEquity: float string,
                            szi: float string,
                            unrealizedPnl: float string
                        },
                        type: "oneWay"
                    }
                ],
                crossMarginSummary: MarginSummary,
                marginSummary: MarginSummary,
                withdrawable: float string,
            }

            其中 MarginSummary 是 {
                    accountValue: float string,
                    totalMarginUsed: float string,
                    totalNtlPos: float string,
                    totalRawUsd: float string,
                }
        """
        return self.post("/info", {"type": "clearinghouseState", "user": address, "dex": dex})

    def spot_user_state(self, address: str) -> Any:
        return self.post("/info", {"type": "spotClearinghouseState", "user": address})

    def open_orders(self, address: str, dex: str = "") -> Any:
        """获取用户的未成交订单。

        POST /info

        参数：
            address (str): 链上地址，42个字符的十六进制格式；
                            例如 0x0000000000000000000000000000000000000000。
        返回： [
            {
                coin: str,
                limitPx: float string,
                oid: int,
                side: "A" | "B",
                sz: float string,
                timestamp: int
            }
        ]
        """
        return self.post("/info", {"type": "openOrders", "user": address, "dex": dex})

    def frontend_open_orders(self, address: str, dex: str = "") -> Any:
        """获取用户的未成交订单及额外的前端信息。

        POST /info

        参数：
            address (str): 链上地址，42个字符的十六进制格式；
                            例如 0x0000000000000000000000000000000000000000。
        返回： [
            {
                children:
                    [
                        前端订单字典
                    ]
                coin: str,
                isPositionTpsl: bool,
                isTrigger: bool,
                limitPx: float string,
                oid: int,
                orderType: str,
                origSz: float string,
                reduceOnly: bool,
                side: "A" | "B",
                sz: float string,
                tif: str,
                timestamp: int,
                triggerCondition: str,
                triggerPx: float str
            }
        ]
        """
        return self.post("/info", {"type": "frontendOpenOrders", "user": address, "dex": dex})

    def all_mids(self, dex: str = "") -> Any:
        """获取所有活跃交易币种的中间价。

        POST /info

        返回：
            {
              ATOM: float string,
              BTC: float string,
              其他正在交易的币种: float string
            }
        """
        return self.post("/info", {"type": "allMids", "dex": dex})

    def user_fills(self, address: str) -> Any:
        """获取指定用户的成交记录。

        POST /info

        参数：
            address (str): 链上地址，42个字符的十六进制格式；
                            例如 0x0000000000000000000000000000000000000000。

        返回：
            [
              {
                closedPnl: float string,
                coin: str,
                crossed: bool,
                dir: str,
                hash: str,
                oid: int,
                px: float string,
                side: str,
                startPosition: float string,
                sz: float string,
                time: int
              },
              ...
            ]
        """
        return self.post("/info", {"type": "userFills", "user": address})

    def user_fills_by_time(
        self, address: str, start_time: int, end_time: Optional[int] = None, aggregate_by_time: Optional[bool] = False
    ) -> Any:
        """按时间获取指定用户的成交记录。

        POST /info

        参数：
            address (str): 链上地址，42个字符的十六进制格式；
                            例如 0x0000000000000000000000000000000000000000。
            start_time (int): Unix时间戳（毫秒）
            end_time (Optional[int]): Unix时间戳（毫秒）
            aggregate_by_time (Optional[bool]): 当为true时，当一个交叉订单被多个不同的挂单成交时，部分成交会被合并。被多个交叉订单成交的挂单不会被聚合。

        返回：
            [
              {
                closedPnl: float string,
                coin: str,
                crossed: bool,
                dir: str,
                hash: str,
                oid: int,
                px: float string,
                side: str,
                startPosition: float string,
                sz: float string,
                time: int
              },
              ...
            ]
        """
        return self.post(
            "/info",
            {
                "type": "userFillsByTime",
                "user": address,
                "startTime": start_time,
                "endTime": end_time,
                "aggregateByTime": aggregate_by_time,
            },
        )

    def meta(self, dex: str = "") -> Meta:
        """获取交易所永续合约元数据

        POST /info

        返回：
            {
                universe: [
                    {
                        name: str,
                        szDecimals: int
                    },
                    ...
                ]
            }
        """
        return cast(Meta, self.post("/info", {"type": "meta", "dex": dex}))

    def meta_and_asset_ctxs(self) -> Any:
        """获取交易所元数据和资产上下文

        POST /info

        返回：
            [
                {
                    universe: [
                        {
                            'name': str,
                            'szDecimals': int
                            'maxLeverage': int,
                            'onlyIsolated': bool,
                        },
                        ...
                    ]
                },
            [
                {
                    "dayNtlVlm": float string,
                    "funding": float string,
                    "impactPxs": Optional([float string, float string]),
                    "markPx": Optional(float string),
                    "midPx": Optional(float string),
                    "openInterest": float string,
                    "oraclePx": float string,
                    "premium": Optional(float string),
                    "prevDayPx": float string
                },
                ...
            ]
        """
        return self.post("/info", {"type": "metaAndAssetCtxs"})

    def perp_dexs(self) -> Any:
        return self.post("/info", {"type": "perpDexs"})

    def spot_meta(self) -> SpotMeta:
        """获取交易所现货元数据

        POST /info

        返回：
            {
                universe: [
                    {
                        tokens: [int, int],
                        name: str,
                        index: int,
                        isCanonical: bool
                    },
                    ...
                ],
                tokens: [
                    {
                        name: str,
                        szDecimals: int,
                        weiDecimals: int,
                        index: int,
                        tokenId: str,
                        isCanonical: bool
                    },
                    ...
                ]
            }
        """
        return cast(SpotMeta, self.post("/info", {"type": "spotMeta"}))

    def spot_meta_and_asset_ctxs(self) -> SpotMetaAndAssetCtxs:
        """获取交易所现货资产上下文
        POST /info
        返回：
            [
                {
                    universe: [
                        {
                            tokens: [int, int],
                            name: str,
                            index: int,
                            isCanonical: bool
                        },
                        ...
                    ],
                    tokens: [
                        {
                            name: str,
                            szDecimals: int,
                            weiDecimals: int,
                            index: int,
                            tokenId: str,
                            isCanonical: bool
                        },
                        ...
                    ]
                },
                [
                    {
                        dayNtlVlm: float string,
                        markPx: float string,
                        midPx: Optional(float string),
                        prevDayPx: float string,
                        circulatingSupply: float string,
                        coin: str
                    }
                    ...
                ]
            ]
        """
        return cast(SpotMetaAndAssetCtxs, self.post("/info", {"type": "spotMetaAndAssetCtxs"}))

    def funding_history(self, name: str, startTime: int, endTime: Optional[int] = None) -> Any:
        """获取指定币种的资金费率历史

        POST /info

        参数：
            name (str): 要获取资金费率历史的币种。
            startTime (int): Unix时间戳（毫秒）。
            endTime (int): Unix时间戳（毫秒）。

        返回：
            [
                {
                    coin: str,
                    fundingRate: float string,
                    premium: float string,
                    time: int
                },
                ...
            ]
        """
        coin = self.name_to_coin[name]
        if endTime is not None:
            return self.post(
                "/info", {"type": "fundingHistory", "coin": coin, "startTime": startTime, "endTime": endTime}
            )
        return self.post("/info", {"type": "fundingHistory", "coin": coin, "startTime": startTime})

    def user_funding_history(self, user: str, startTime: int, endTime: Optional[int] = None) -> Any:
        """获取用户的资金费率历史
        POST /info
        参数：
            user (str): 用户地址，42个字符的十六进制格式。
            startTime (int): 开始时间（毫秒），包含。
            endTime (int, optional): 结束时间（毫秒），包含。默认为当前时间。
        返回：
            List[Dict]: 资金费率历史记录列表，每条记录包含：
                - user (str): 用户地址。
                - type (str): 记录类型，例如 "userFunding"。
                - startTime (int): 开始时间的Unix时间戳（毫秒）。
                - endTime (int): 结束时间的Unix时间戳（毫秒）。
        """
        if endTime is not None:
            return self.post("/info", {"type": "userFunding", "user": user, "startTime": startTime, "endTime": endTime})
        return self.post("/info", {"type": "userFunding", "user": user, "startTime": startTime})

    def l2_snapshot(self, name: str) -> Any:
        """获取指定币种的二级订单簿快照

        POST /info

        参数：
            name (str): 要获取二级订单簿快照的币种。

        返回：
            {
                coin: str,
                levels: [
                    [
                        {
                            n: int,
                            px: float string,
                            sz: float string
                        },
                        ...
                    ],
                    ...
                ],
                time: int
            }
        """
        if name in self.name_to_coin:
            return self.post("/info", {"type": "l2Book", "coin": self.name_to_coin[name]})
        return {}
    def candles_snapshot(self, name: str, interval: str, startTime: int, endTime: int) -> Any:
        """获取指定币种的K线快照

        POST /info

        参数：
            name (str): 要获取K线快照的币种。
            interval (str): K线间隔。
            startTime (int): Unix时间戳（毫秒）。
            endTime (int): Unix时间戳（毫秒）。

        返回：
            [
                {
                    T: int,
                    c: float string,
                    h: float string,
                    i: str,
                    l: float string,
                    n: int,
                    o: float string,
                    s: string,
                    t: int,
                    v: float string
                },
                ...
            ]
        """
        if name in self.name_to_coin:
            req = {"coin": self.name_to_coin[name], "interval": interval, "startTime": startTime, "endTime": endTime}
            return self.post("/info", {"type": "candleSnapshot", "req": req})
        return []
    def user_fees(self, address: str) -> Any:
        """获取与用户相关的交易量活动。
        POST /info
        参数：
            address (str): 链上地址，42个字符的十六进制格式；
                            例如 0x0000000000000000000000000000000000000000。
        返回：
            {
                activeReferralDiscount: float string,
                dailyUserVlm: [
                    {
                        date: str,
                        exchange: str,
                        userAdd: float string,
                        userCross: float string
                    },
                ],
                feeSchedule: {
                    add: float string,
                    cross: float string,
                    referralDiscount: float string,
                    tiers: {
                        mm: [
                            {
                                add: float string,
                                makerFractionCutoff: float string
                            },
                        ],
                        vip: [
                            {
                                add: float string,
                                cross: float string,
                                ntlCutoff: float string
                            },
                        ]
                    }
                },
                userAddRate: float string,
                userCrossRate: float string
            }
        """
        return self.post("/info", {"type": "userFees", "user": address})

    def user_staking_summary(self, address: str) -> Any:
        """获取与用户相关的质押摘要。
        POST /info
        参数：
            address (str): 链上地址，42个字符的十六进制格式；
                            例如 0x0000000000000000000000000000000000000000。
        返回：
            {
                delegated: float string,
                undelegated: float string,
                totalPendingWithdrawal: float string,
                nPendingWithdrawals: int
            }
        """
        return self.post("/info", {"type": "delegatorSummary", "user": address})

    def user_staking_delegations(self, address: str) -> Any:
        """获取用户的质押委托。
        POST /info
        参数：
            address (str): 链上地址，42个字符的十六进制格式；
                            例如 0x0000000000000000000000000000000000000000。
        返回：
            [
                {
                    validator: string,
                    amount: float string,
                    lockedUntilTimestamp: int
                },
            ]
        """
        return self.post("/info", {"type": "delegations", "user": address})

    def user_staking_rewards(self, address: str) -> Any:
        """获取与用户相关的历史质押奖励。
        POST /info
        参数：
            address (str): 链上地址，42个字符的十六进制格式；
                            例如 0x0000000000000000000000000000000000000000。
        返回：
            [
                {
                    time: int,
                    source: string,
                    totalAmount: float string
                },
            ]
        """
        return self.post("/info", {"type": "delegatorRewards", "user": address})

    def delegator_history(self, user: str) -> Any:
        """获取用户的全面质押历史。

        POST /info

        参数：
            user (str): 链上地址，42个字符的十六进制格式。

        返回：
            全面的质押历史，包括委托和取消委托事件，
            带有时间戳、交易哈希和详细的增量信息。
        """
        return self.post("/info", {"type": "delegatorHistory", "user": user})

    def query_order_by_oid(self, user: str, oid: int) -> Any:
        return self.post("/info", {"type": "orderStatus", "user": user, "oid": oid})

    def query_order_by_cloid(self, user: str, cloid: Cloid) -> Any:
        return self.post("/info", {"type": "orderStatus", "user": user, "oid": cloid.to_raw()})

    def query_referral_state(self, user: str) -> Any:
        return self.post("/info", {"type": "referral", "user": user})

    def query_sub_accounts(self, user: str) -> Any:
        return self.post("/info", {"type": "subAccounts", "user": user})

    def query_user_to_multi_sig_signers(self, multi_sig_user: str) -> Any:
        return self.post("/info", {"type": "userToMultiSigSigners", "user": multi_sig_user})

    def query_perp_deploy_auction_status(self) -> Any:
        return self.post("/info", {"type": "perpDeployAuctionStatus"})
    
    def query_user_dex_abstraction_state(self, user: str) -> Any:
        return self.post("/info", {"type": "userDexAbstraction", "user": user})
    
    def historical_orders(self, user: str) -> Any:
        """获取用户的历史订单。

        POST /info

        参数：
            user (str): 链上地址，42个字符的十六进制格式；
                        例如 0x0000000000000000000000000000000000000000。

        返回：
            最多返回2000条最近的历史订单，
            包含其当前状态和详细的订单信息。
        """
        return self.post("/info", {"type": "historicalOrders", "user": user})

    def user_non_funding_ledger_updates(self, user: str, startTime: int, endTime: Optional[int] = None) -> Any:
        """获取用户的非资金费率账本更新。

        POST /info

        参数：
            user (str): 链上地址，42个字符的十六进制格式。
            startTime (int): 开始时间（毫秒，epoch时间戳）。
            endTime (Optional[int]): 结束时间（毫秒，epoch时间戳）。

        返回：
            全面的账本更新，包括存款、取款、转账、
            清算和其他账户活动，不包括资金费率支付。
        """
        return self.post(
            "/info",
            {"type": "userNonFundingLedgerUpdates", "user": user, "startTime": startTime, "endTime": endTime},
        )

    def portfolio(self, user: str) -> Any:
        """获取全面的投资组合表现数据。

        POST /info

        参数：
            user (str): 链上地址，42个字符的十六进制格式。

        返回：
            不同时间段的全面投资组合表现数据，
            包括账户价值历史、盈亏历史和交易量指标。
        """
        return self.post("/info", {"type": "portfolio", "user": user})

    def user_twap_slice_fills(self, user: str) -> Any:
        """获取用户的TWAP分片成交记录。

        POST /info

        参数：
            user (str): 链上地址，42个字符的十六进制格式。

        返回：
            最多返回2000条最近的TWAP分片成交记录，
            包含详细的执行信息。
        """
        return self.post("/info", {"type": "userTwapSliceFills", "user": user})

    def user_vault_equities(self, user: str) -> Any:
        """获取用户在所有金库中的权益头寸。

        POST /info

        参数：
            user (str): 链上地址，42个字符的十六进制格式。

        返回：
            用户在所有金库中的权益头寸的详细信息，
            包括当前价值、盈亏指标和提款详情。
        """
        return self.post("/info", {"type": "userVaultEquities", "user": user})

    def user_role(self, user: str) -> Any:
        """获取用户的角色和账户类型信息。

        POST /info

        参数：
            user (str): 链上地址，42个字符的十六进制格式。

        返回：
            角色和账户类型信息，包括账户结构、
            权限以及在Hyperliquid生态系统中的关系。
        """
        return self.post("/info", {"type": "userRole", "user": user})

    def user_rate_limit(self, user: str) -> Any:
        """获取用户的API速率限制配置和使用情况。

        POST /info

        参数：
            user (str): 链上地址，42个字符的十六进制格式。

        返回：
            用户API速率限制配置和当前使用情况的详细信息，
            用于管理API使用并避免速率限制。
        """
        return self.post("/info", {"type": "userRateLimit", "user": user})

    def query_spot_deploy_auction_status(self, user: str) -> Any:
        return self.post("/info", {"type": "spotDeployState", "user": user})

    def _remap_coin_subscription(self, subscription: Subscription) -> None:
        if (
            subscription["type"] == "l2Book"
            or subscription["type"] == "trades"
            or subscription["type"] == "candle"
            or subscription["type"] == "bbo"
            or subscription["type"] == "activeAssetCtx"
        ):
            if subscription["coin"] in self.name_to_coin:
                subscription["coin"] = self.name_to_coin[subscription["coin"]]

    def subscribe(self, subscription: Subscription, callback: Callable[[Any], None]) -> int:
        self._remap_coin_subscription(subscription)
        if self.ws_manager is None:
            raise RuntimeError("无法调用 subscribe，因为使用了 skip_ws")
        else:
            return self.ws_manager.subscribe(subscription, callback)

    def unsubscribe(self, subscription: Subscription, subscription_id: int) -> bool:
        self._remap_coin_subscription(subscription)
        if self.ws_manager is None:
            raise RuntimeError("无法调用 unsubscribe，因为使用了 skip_ws")
        else:
            return self.ws_manager.unsubscribe(subscription, subscription_id)

    def name_to_asset(self, name: str) -> int:
        if name not in self.name_to_coin:
            return {"error":f"{name}不在name_to_coin字典中"}
        return self.coin_to_asset[self.name_to_coin[name]]
