# position_controller_s1.py
import time
import asyncio
import logging
import math # 需要 math 来处理精度

class PositionControllerS1:
    """
    独立的仓位控制策略 (S1)。
    基于每日更新的52日高低点，高频检查仓位并执行调整。
    独立于主网格策略运行，不修改网格的 base_price。
    """
    def __init__(self, trader_instance):
        """
        初始化S1仓位控制器。

        Args:
            trader_instance: 主 GridTrader 类的实例，用于访问交易所客户端、
                             获取账户信息、执行订单和日志记录。
        """
        self.trader = trader_instance  # 保存对主 trader 实例的引用
        self.config = trader_instance.config # 访问配置
        self.logger = logging.getLogger(self.__class__.__name__) # 创建独立的 logger

        # S1 策略参数 (从配置或直接赋值)
        # 确保这些参数在你的 config.py 或 trader_instance 中可访问
        self.s1_lookback = getattr(self.config, 'S1_LOOKBACK', 52)
        self.s1_sell_target_pct = getattr(self.config, 'S1_SELL_TARGET_PCT', 0.50)
        self.s1_buy_target_pct = getattr(self.config, 'S1_BUY_TARGET_PCT', 0.70)

        # S1 状态变量
        self.s1_daily_high = None
        self.s1_daily_low = None
        self.s1_last_data_update_ts = 0
        # 每日更新时间间隔（秒），略小于24小时确保不会错过
        self.daily_update_interval = 23.9 * 60 * 60

        # 新增趋势状态属性
        self.trend_status = {
            'short_ema_bullish': None, # 例如 12 EMA > 26 EMA
            'macd_bullish': None,      # 例如 MACD Histogram > 0
            'long_ema_bullish': None   # 例如 Current Price > 200-day EMA
        }
        self.allow_replenish_based_on_trend = False # 综合趋势判断结果
        
        self.logger.info(f"S1 Position Controller initialized. Lookback={self.s1_lookback} days, Sell Target={self.s1_sell_target_pct*100}%, Buy Target={self.s1_buy_target_pct*100}%. Trend check also active.")

    async def _fetch_and_calculate_s1_levels(self):
        """获取日线数据并计算S1策略所需的回看期高低点"""
        try:
            # 获取比回看期稍多的日线数据 (+2 buffer)
            limit = self.s1_lookback + 2
            klines = await self.trader.exchange.fetch_ohlcv(
                self.trader.symbol, 
                timeframe='1d', 
                limit=limit
            )

            if not klines or len(klines) < self.s1_lookback + 1:
                self.logger.warning(f"S1: Insufficient daily klines received ({len(klines)}), cannot update levels.")
                return False

            # 使用倒数第2根K线往前数 s1_lookback 根来计算 (排除最新未完成K线)
            # klines[-1] 是当前未完成日线，klines[-2] 是昨天收盘的日线
            relevant_klines = klines[-(self.s1_lookback + 1) : -1]

            if len(relevant_klines) < self.s1_lookback:
                 self.logger.warning(f"S1: Not enough relevant klines ({len(relevant_klines)}) for lookback {self.s1_lookback}.")
                 return False

            # 计算高低点 (索引 2 是 high, 3 是 low)
            self.s1_daily_high = max(float(k[2]) for k in relevant_klines)
            self.s1_daily_low = min(float(k[3]) for k in relevant_klines)
            self.s1_last_data_update_ts = time.time()
            self.logger.info(f"S1 Levels Updated: High={self.s1_daily_high:.4f}, Low={self.s1_daily_low:.4f}")
            return True

        except Exception as e:
            self.logger.error(f"S1: Failed to fetch or calculate daily levels: {e}", exc_info=False)
            return False

    async def update_daily_s1_levels(self):
        """
        每日检查并更新一次S1所需的回看期高低价，并进行趋势判断。
        """
        now = time.time()
        s1_levels_updated_this_cycle = False
        if now - self.s1_last_data_update_ts >= self.daily_update_interval:
            self.logger.info("S1: Time to update daily high/low levels for S1 strategy...")
            s1_levels_updated_this_cycle = await self._fetch_and_calculate_s1_levels()
        
        # --- 趋势判断逻辑 ---
        try:
            self.logger.debug("S1 Controller: Updating trend analysis for replenish decisions...")

            # 1. 短期趋势: 12 EMA vs 26 EMA (使用1小时图)
            short_ma, long_ma_for_short_trend = await self.trader.get_ma_data(short_period=12, long_period=26)
            if short_ma is not None and long_ma_for_short_trend is not None:
                self.trend_status['short_ema_bullish'] = short_ma > long_ma_for_short_trend
                self.logger.debug(f"Trend: Short (1h 12EMA > 26EMA): {self.trend_status['short_ema_bullish']} (EMA12: {short_ma:.4f}, EMA26: {long_ma_for_short_trend:.4f})")
            else:
                self.trend_status['short_ema_bullish'] = None

            # 2. 中期趋势: MACD (使用1小时图)
            macd_line, signal_line = await self.trader.get_macd_data()
            if macd_line is not None and signal_line is not None:
                macd_histogram = macd_line - signal_line
                self.trend_status['macd_bullish'] = macd_histogram > 0
                self.logger.debug(f"Trend: Medium (1h MACD Hist > 0): {self.trend_status['macd_bullish']} (MACD: {macd_line:.4f}, Signal: {signal_line:.4f}, Hist: {macd_histogram:.4f})")
            else:
                self.trend_status['macd_bullish'] = None

            # 3. 长期趋势: 价格 vs 200日EMA (使用日线图)
            current_price = self.trader.current_price # 使用trader中缓存的当前价格
            if not current_price: # 如果trader没有当前价格，尝试获取一次
                 current_price = await self.trader._get_latest_price()

            ema_200_period = 200
            ohlcv_daily_for_long_ema = await self.trader.exchange.fetch_ohlcv(
                self.trader.symbol,
                timeframe='1d',
                limit=ema_200_period + 50
            )
            if current_price is not None and ohlcv_daily_for_long_ema and len(ohlcv_daily_for_long_ema) >= ema_200_period:
                closes_daily = [candle[4] for candle in ohlcv_daily_for_long_ema]
                # 确保 _calculate_ema 方法存在于 self.trader
                if hasattr(self.trader, '_calculate_ema') and callable(self.trader._calculate_ema):
                    ema_200_daily = self.trader._calculate_ema(closes_daily, ema_200_period)
                    if ema_200_daily != 0 :
                        self.trend_status['long_ema_bullish'] = current_price > ema_200_daily
                        self.logger.debug(f"Trend: Long (Price > 200D EMA): {self.trend_status['long_ema_bullish']} (Price: {current_price:.4f}, EMA200D: {ema_200_daily:.4f})")
                    else:
                        self.trend_status['long_ema_bullish'] = None
                else:
                    self.logger.warning("Trend: self.trader._calculate_ema method not found!")
                    self.trend_status['long_ema_bullish'] = None
            else:
                self.trend_status['long_ema_bullish'] = None
                self.logger.warning(f"Trend: Insufficient data for 200D EMA or current price unavailable. Daily klines: {len(ohlcv_daily_for_long_ema) if ohlcv_daily_for_long_ema else 'None'}, Current Price: {current_price}")

            # 综合判断是否允许补仓 (严格的三趋势共振：短、中、长期趋势都为True)
            if self.trend_status['short_ema_bullish'] is True and \
               self.trend_status['macd_bullish'] is True and \
               self.trend_status['long_ema_bullish'] is True:
                self.allow_replenish_based_on_trend = True
                self.logger.info("三趋势共振判断：允许基于趋势补仓。")
            else:
                self.allow_replenish_based_on_trend = False
                self.logger.info(f"三趋势共振判断：不允许基于趋势补仓。详细状态: Short={self.trend_status['short_ema_bullish']}, Mid(MACD)={self.trend_status['macd_bullish']}, Long={self.trend_status['long_ema_bullish']}")
            
            # self.logger.info(f"Trend analysis for replenish updated: Allow={self.allow_replenish_based_on_trend}, Status={self.trend_status}") # 上面已有更详细日志

        except Exception as e:
            self.logger.error(f"S1 Controller: Failed during trend analysis: {e}", exc_info=True)
            self.allow_replenish_based_on_trend = False # 保守处理

    async def _execute_s1_adjustment(self, side, amount_bnb):
        """
        专门执行 S1 仓位调整的下单函数。
        调用 trader 实例的 execute_order 方法。
        不更新网格的 base_price。
        """
        try:
            # 1. 精度调整
            if hasattr(self.trader, '_adjust_amount_precision') and callable(self.trader._adjust_amount_precision):
                adjusted_amount = self.trader._adjust_amount_precision(amount_bnb)
            else:
                precision = 3
                factor = 10 ** precision
                adjusted_amount = math.floor(amount_bnb * factor) / factor
                self.logger.warning("S1: Using basic amount precision adjustment.")

            if adjusted_amount <= 0:
                self.logger.warning(f"S1: Adjusted amount is zero or negative ({adjusted_amount}), skipping order.")
                return False

            # 2. 获取当前价格
            current_price = self.trader.current_price
            if not current_price or current_price <= 0:
                self.logger.error("S1: Invalid current price, cannot execute adjustment.")
                return False
            
            # 3. 计算目标USDT金额
            target_amount_usdt = adjusted_amount * current_price

            # 4. 检查最小订单限制
            min_notional = 10  # 默认最小名义价值 (USDT)
            min_amount_limit = 0.0001 # 默认最小数量
            if hasattr(self.trader, 'symbol_info') and self.trader.symbol_info:
                limits = self.trader.symbol_info.get('limits', {})
                min_notional = limits.get('cost', {}).get('min', min_notional)
                min_amount_limit = limits.get('amount', {}).get('min', min_amount_limit)
            
            if adjusted_amount < min_amount_limit:
                self.logger.warning(f"S1: Adjusted amount {adjusted_amount:.8f} BNB is below minimum amount limit {min_amount_limit:.8f}.")
                return False
            if target_amount_usdt < min_notional:
                self.logger.warning(f"S1: Order value {target_amount_usdt:.2f} USDT is below minimum notional value {min_notional:.2f}.")
                return False

            self.logger.info(f"S1: Attempting to {side} {adjusted_amount:.8f} BNB (approx {target_amount_usdt:.2f} USDT) via trader.execute_order.")

            # 5. 调用 trader.execute_order
            # 注意：trader.execute_order 将处理余额检查和资金划转
            order_result = await self.trader.execute_order(
                side=side.lower(),
                target_amount_usdt=target_amount_usdt
            )

            if order_result and order_result.get('id'):
                self.logger.info(f"S1: Adjustment order processed by trader.execute_order. Order ID: {order_result.get('id')}")
                
                # 6. （可选）更新交易记录器 (如果希望S1交易也记录在案)
                # trader.execute_order 内部已经有详细的交易记录逻辑，这里可以简化或移除
                # 但如果需要特别标记 S1 策略的交易，可以保留部分
                if hasattr(self.trader, 'order_tracker'):
                    trade_info = {
                        'timestamp': time.time(),
                        'strategy': 'S1', # 标记来源
                        'side': side,
                        # 使用 order_result 中的成交价格和数量
                        'price': float(order_result.get('price', current_price)),
                        'amount': float(order_result.get('filled', adjusted_amount)),
                        'cost': float(order_result.get('cost', target_amount_usdt)),
                        'fee': order_result.get('fee', {}).get('cost', 0),
                        'order_id': order_result.get('id')
                    }
                    # self.trader.order_tracker.add_trade(trade_info) # trader.execute_order 内部会记录
                    self.logger.info(f"S1: Trade details from execute_order - Price: {trade_info['price']}, Amount: {trade_info['amount']}, Cost: {trade_info['cost']}")

                # 7. 买入后如有多余资金，转入理财 (trader.execute_order 内部也会调用 _transfer_excess_funds)
                # 此处的调用可以视为一个额外的保障，或者如果 trader.execute_order 的调用时机不同，则保留
                if side == 'BUY' and hasattr(self.trader, '_transfer_excess_funds'):
                    try:
                        # self.logger.info("S1: Post-BUY, ensuring excess funds are transferred by trader's internal call.")
                        # await self.trader._transfer_excess_funds() # 通常由 trader.execute_order 内部处理
                        pass # 假设 trader.execute_order 内部已处理
                    except Exception as e:
                        self.logger.warning(f"S1: Error during post-BUY _transfer_excess_funds (likely already handled): {e}")
                
                return True # 表示成功委托
            else:
                self.logger.error(f"S1: trader.execute_order failed for {side} {adjusted_amount:.8f} BNB.")
                return False

        except Exception as e:
            self.logger.error(f"S1: Failed to execute adjustment order ({side} {amount_bnb:.8f} BNB): {e}", exc_info=True)
            return False


    async def check_and_execute(self):
        """
        高频检查 S1 仓位控制条件并执行调仓。
        应在主交易循环中频繁调用。
        """
        # 0. 确保我们有当天的 S1 边界值
        if self.s1_daily_high is None or self.s1_daily_low is None:
            self.logger.debug("S1: Daily high/low levels not available yet.")
            return # 等待下次数据更新

        # 1. 获取当前状态 (通过 trader 实例)
        try:
            current_price = self.trader.current_price
            if not current_price or current_price <= 0:
                self.logger.warning("S1: Invalid current price from trader.")
                return

            # 使用风控管理器的仓位计算方法
            position_pct = await self.trader.risk_manager._get_position_ratio()
            position_value = await self.trader.risk_manager._get_position_value()
            total_assets = await self.trader._get_total_assets()
            bnb_balance = await self.trader.get_available_balance('BNB') # 获取可用 BNB

            if total_assets <= 0:
                self.logger.warning("S1: Invalid total assets value.")
                return

        except Exception as e:
            self.logger.error(f"S1: Failed to get current state: {e}")
            return

        # 2. 判断 S1 条件
        s1_action = 'NONE'
        s1_trade_amount_bnb = 0

        # 高点检查
        if current_price > self.s1_daily_high and position_pct > self.s1_sell_target_pct:
            s1_action = 'SELL'
            target_position_value = total_assets * self.s1_sell_target_pct
            sell_value_needed = position_value - target_position_value
            # 确保不会卖出负数或零 (以防万一)
            if sell_value_needed > 0:
                s1_trade_amount_bnb = min(sell_value_needed / current_price, bnb_balance)
                self.logger.info(f"S1: High level breached. Need to SELL {s1_trade_amount_bnb:.8f} BNB to reach {self.s1_sell_target_pct*100:.0f}% target.")
            else:
                s1_action = 'NONE' # 重置，因为计算结果无效

        # 低点检查 (用 elif 避免同时触发)
        elif current_price < self.s1_daily_low and position_pct < self.s1_buy_target_pct:
            s1_action = 'BUY'
            target_position_value = total_assets * self.s1_buy_target_pct
            buy_value_needed = target_position_value - position_value
            # 确保不会买入负数或零
            if buy_value_needed > 0:
                s1_trade_amount_bnb = buy_value_needed / current_price
                self.logger.info(f"S1: Low level breached. Need to BUY {s1_trade_amount_bnb:.8f} BNB to reach {self.s1_buy_target_pct*100:.0f}% target.")
            else:
                s1_action = 'NONE' # 重置

        # 3. 如果触发，执行 S1 调仓
        if s1_action != 'NONE' and s1_trade_amount_bnb > 1e-9: # 加个极小值判断
            self.logger.info(f"S1: Condition met for {s1_action} adjustment.")
            await self._execute_s1_adjustment(s1_action, s1_trade_amount_bnb)
            # 注意：这里不等待执行结果，执行函数内部处理日志和错误
            # 也不更新网格的 base_price
        # else:
            # self.logger.debug(f"S1: No adjustment needed. Price={current_price:.2f} H={self.s1_daily_high:.2f} L={self.s1_daily_low:.2f} Pos={position_pct:.2%}") 