"""
趋势票分析模块（独立模块，不改造原有功能）

基于333文件的趋势票100分制评分标准
"""

import logging
import sqlite3
import time
import random
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor
from data.database import DB_PATH
import os

if TYPE_CHECKING:
    import requests  # type: ignore[import-not-found]
    import pandas as pd  # type: ignore[import-not-found]
    import numpy as np  # type: ignore[import-not-found]

# 网络/外部库改为可选依赖
try:
    import requests  # type: ignore[assignment]
except ImportError:
    requests = None  # type: ignore[assignment]
    logging.getLogger(__name__).warning("requests 未安装, 趋势票 K 线抓取不可用")

try:
    import pandas as pd  # type: ignore[assignment]
except ImportError:
    pd = None  # type: ignore[assignment]
    logging.getLogger(__name__).warning("pandas 未安装, 趋势票数据处理受限")

try:
    import numpy as np  # type: ignore[assignment]
except ImportError:
    np = None  # type: ignore[assignment]
    logging.getLogger(__name__).warning("numpy 未安装, 趋势票计算受限")

logger = logging.getLogger(__name__)

# 禁用代理（解决网络连接问题）
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if key in os.environ:
        del os.environ[key]

# 导入 API 速率限制器
from .api_rate_limiter import get_api_rate_limiter, RateLimitConfig

# 趋势标的专用配置（更宽松，防封禁）
trend_limiter_config = RateLimitConfig(
    min_interval=3.0,      # 最小间隔 3.0 秒（避免封禁）
    max_interval=6.0,      # 最大间隔 6.0 秒
    batch_size=3,          # 每3次请求后休眠
    batch_sleep=5.0,       # 批量休眠 5 秒
    enable_jitter=True
)
trend_limiter = get_api_rate_limiter(trend_limiter_config)


class TrendStockAnalyzer:
    """
    趋势票分析器
    
    独立模块，负责：
    1. 获取历史K线数据（通过东方财富API）
    2. 计算技术指标（MA, 量比, 回撤等）
    3. 100分制评分（基于333文件）
    4. 存储和查询趋势票数据
    """
    
    # 防封禁配置（这些值会作为默认值，但不实际使用，因为使用了速率限制器）
    SLEEP_MIN = 2.0
    SLEEP_MAX = 5.0

    # User-Agent池（防封）
    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    ]

    def __init__(self):
        self.db_path = DB_PATH
        self._last_request_time = None

        # 禁用代理（解决网络连接问题）
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.proxies = {'http': '', 'https': ''}

        # 使用速率限制器
        self.limiter = trend_limiter
    
    def _random_sleep(self, min_sec: float = None, max_sec: float = None) -> None:
        """随机休眠（防封禁）"""
        min_sec = min_sec or self.SLEEP_MIN
        max_sec = max_sec or self.SLEEP_MAX
        sleep_time = random.uniform(min_sec, max_sec)
        
        # 检查上次请求时间，确保最小间隔
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < min_sec:
                time.sleep(min_sec - elapsed)
        
        # 执行随机休眠
        time.sleep(sleep_time)
        self._last_request_time = time.time()
    
    def _get_db_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        return sqlite3.connect(self.db_path)

    async def _run_in_thread(self, func, *args, **kwargs):
        """在线程池中运行同步函数，避免阻塞事件循环"""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = await loop.run_in_executor(executor, func, *args, **kwargs)
        return result

    def _get_market_id(self, stock_code: str) -> str:
        """
        根据股票代码获取市场ID（用于东方财富API）
        
        参数：
        - 6开头：上海主板 -> 1
        - 0/3开头：深圳主板/创业板 -> 0
        - 8/4开头：北京股票 -> 0
        """
        if stock_code.startswith('6'):
            return '1'
        elif stock_code.startswith('0') or stock_code.startswith('3'):
            return '0'
        elif stock_code.startswith('8') or stock_code.startswith('4'):
            return '0'
        else:
            return '1'
    
    def fetch_stock_kline(self, stock_code: str, days: int = 90) -> Optional[pd.DataFrame]:
        """
        获取单只股票的历史K线数据

        使用东方财富K线API（免费、无Token）

        参数：
        - stock_code: 股票代码（如'600519'）
        - days: 获取天数（默认90天）

        返回：
        - DataFrame: 包含OHLCV数据的DataFrame，失败返回None
        """
        klines = None
        max_retries = 3

        for attempt in range(max_retries):
            try:
                # 使用速率限制器等待（防封禁）
                api_name = f"eastmoney_kline_{stock_code}"
                if attempt == 0:
                    # 第一次请求前等待
                    self.limiter.wait_before_request(api_name)
                else:
                    # 重试时额外等待
                    time.sleep(3)

                # 计算日期范围
                end_date = datetime.now().strftime('%Y%m%d')
                start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

                # 准备API参数
                market_id = self._get_market_id(stock_code)
                secid = f"{market_id}.{stock_code}"

                params = {
                    'fields1': 'f1,f2,f3,f4,f5,f6',
                    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116',
                    'ut': '7eea3edcaed734bea9cbfc24409ed989',
                    'klt': '101',  # 日线
                    'fqt': '1',  # 前复权
                    'secid': secid,
                    'beg': start_date,
                    'end': end_date,
                    '_': str(int(datetime.now().timestamp() * 1000))
                }

                # 随机选择User-Agent
                headers = {
                    'User-Agent': random.choice(self.USER_AGENTS)
                }

                # 调用东方财富K线API
                response = self.session.get(
                    'https://push2his.eastmoney.com/api/qt/stock/kline/get',
                    params=params,
                    headers=headers,
                    timeout=30
                )

                data_json = response.json()

                if not (data_json.get("data") and data_json["data"].get("klines")):
                    logger.warning(f"股票 {stock_code} 返回空数据")
                    self.limiter.record_failure(api_name, "返回空数据")
                    return None

                # 解析K线数据
                klines = data_json["data"]["klines"]

                # 记录成功
                self.limiter.record_success(api_name)
                break  # 成功获取，退出重试循环

            except Exception as e:
                logger.warning(f"股票 {stock_code} 获取失败（尝试 {attempt + 1}/{max_retries}）: {str(e)[:100]}")
                if attempt == max_retries - 1:
                    logger.error(f"获取股票 {stock_code} K线数据失败（已重试{max_retries}次）")
                    return None

        # 转换为DataFrame
        try:
            df_data = []
            for kline in klines:
                parts = kline.split(',')
                df_data.append({
                    'date': parts[0],
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': float(parts[5]),
                    'amount': float(parts[6]),
                    'amplitude': float(parts[7]),
                    'change_pct': float(parts[8]),
                    'change_amount': float(parts[9]),
                    'turnover': float(parts[10])
                })

            df = pd.DataFrame(df_data)
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

            return df

        except Exception as e:
            logger.error(f"解析股票 {stock_code} K线数据失败: {e}", exc_info=True)
            return None
    
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算技术指标（升级版v2.0）
        
        新增：
        - MA20斜率（判断趋势强度）
        - 智能计算60日涨幅（如果数据不足90天，按实际天数推算）
        """
        df = df.copy()
        
        # 计算均线
        df['ma5'] = df['close'].rolling(window=5).mean()
        df['ma10'] = df['close'].rolling(window=10).mean()
        df['ma20'] = df['close'].rolling(window=20).mean()
        
        # 计算MA60（如果数据足够）
        if len(df) >= 60:
            df['ma60'] = df['close'].rolling(window=60).mean()
        else:
            df['ma60'] = df['ma20']  # 数据不足时使用MA20替代
        
        # 计算量比（当日成交量 / 5日均量）
        df['vol_ma5'] = df['volume'].rolling(window=5).mean()
        df['volume_ratio'] = df['volume'] / df['vol_ma5']
        
        # 计算近N日涨幅（优先用60日，不足60天就用可用天数）
        available_days = len(df)
        if available_days >= 20:
            shift_days = min(available_days - 1, 60)  # 减去1，避免shift导致NaN
            df['change_pct_60d'] = (df['close'] - df['close'].shift(shift_days)) / df['close'].shift(shift_days) * 100
        else:
            df['change_pct_60d'] = 0  # 数据太少，无法计算
        
        # 计算MA20斜率（最新3日，判断趋势强度）
        if len(df) >= 3:
            recent_ma20 = df['ma20'].tail(3).dropna()
            if len(recent_ma20) >= 3:
                # 简单斜率：(最新 - 3日前) / 3日前
                ma20_slope = (recent_ma20.iloc[-1] - recent_ma20.iloc[0]) / recent_ma20.iloc[0] * 100
                df['ma20_slope'] = ma20_slope
            else:
                df['ma20_slope'] = 0
        else:
            df['ma20_slope'] = 0
        
        # 计算20日回撤
        df['rolling_max'] = df['close'].rolling(window=20).max()
        df['drawdown_20d'] = (df['close'] - df['rolling_max']) / df['rolling_max'] * 100
        
        return df
    
    def _calculate_trend_score(self, df: pd.DataFrame, stock_id: int = None) -> Dict:
        """
        计算趋势票评分（升级版v3.0 - 100分制）

        基于专家建议优化：
        - 均线多头排列（MA5>MA10>MA20）：25分
        - MA20斜率为正（>0%）：15分
        - 60日涨幅区间扩展：20%-80% ⇒ 20分，80%-120% ⇒ 15分，>120% ⇒ 10分
        - 题材强度：关联题材今日涨停>5只 ⇒ 10分（否则0分）
        - 维持量能：1-3倍均值 ⇒ 10分；突破放量3-5倍 ⇒ 5分；爆量>5倍 ⇒ 0分
        - 20日回撤 < 12%：10分

        返回结果（与数据库表字段兼容）：
        - total_score: 总分（0-100）
        - ma_score: MA多头得分
        - ma20_slope_score: MA20斜率得分
        - gain_60d_score: 60日涨幅得分
        - sector_score: 题材强度得分
        - volume_score: 量能趋势得分
        - recent_score: MA20斜率得分（兼容旧字段）
        - drawdown_score: 回撤控制得分
        - trend_level: 趋势等级
        """
        if df is None or len(df) < 20:
            return {
                'total_score': 0,
                'ma_score': 0,
                'gain_60d_score': 0,
                'volume_score': 0,
                'recent_score': 0,
                'ma60_score': 0,
                'reason': '数据不足'
            }

        latest = df.iloc[-1]
        scores = {}

        # 1. 均线多头排列（25分）
        ma_score = 0
        if latest['ma5'] > latest['ma10'] > latest['ma20']:
            ma_score = 25
        elif latest['ma5'] > latest['ma10']:
            ma_score = 15
        scores['ma_score'] = ma_score

        # 2. MA20斜率为正（15分）
        ma20_slope_score = 0
        if latest['ma20_slope'] > 0:
            if latest['ma20_slope'] > 2:
                ma20_slope_score = 15
            elif latest['ma20_slope'] > 1:
                ma20_slope_score = 12
            else:
                ma20_slope_score = 9
        scores['recent_score'] = ma20_slope_score

        # 3. 60日涨幅（20分，根据专家建议扩展区间）
        gain_60d = latest.get('change_pct_60d', 0)
        gain_60d_score = 0
        if 20 <= gain_60d <= 80:
            gain_60d_score = 20
        elif 80 < gain_60d <= 120:
            gain_60d_score = 15
        elif gain_60d > 120:
            gain_60d_score = 10
        scores['gain_60d_score'] = gain_60d_score

        # 4. 题材强度（10分，关联题材今日涨停>5只）
        sector_score = 0
        if stock_id:
            try:
                import sqlite3
                from src.utils import get_latest_trading_date_from_db

                conn = sqlite3.connect("data/fupan.db")
                cursor = conn.cursor()

                # 获取最新交易日
                latest_date = get_latest_trading_date_from_db()
                if not latest_date:
                    latest_date = df.iloc[-1]['date'] if 'date' in df.columns else None

                if latest_date:
                    # 查询该股票关联的题材中，今日涨停超过5只的题材数
                    cursor.execute('''
                        SELECT COUNT(DISTINCT ta.topic_id)
                        FROM topic_stock_relations tsr
                        JOIN topics t ON tsr.topic_id = t.topic_id
                        JOIN first_limits fl ON tsr.stock_id = fl.stock_id
                        JOIN topic_activations ta ON tsr.topic_id = ta.topic_id
                        WHERE tsr.stock_id = ?
                          AND fl.limit_date = ?
                          AND ta.activation_date = ?
                    ''', (stock_id, latest_date, latest_date))

                    strong_topic_count = cursor.fetchone()[0]
                    if strong_topic_count > 0:
                        sector_score = 10

                conn.close()
            except Exception as e:
                pass  # 查询失败不影响其他评分
        scores['sector_score'] = sector_score

        # 5. 量能趋势（10分，根据专家建议调整为三档）
        volume_score = 0
        if len(df) >= 3:
            recent_vols = df['volume'].tail(3).values
            avg_vol = df['volume'].tail(10).mean()

            # 判断量能类型
            max_vol_ratio = max(v / avg_vol for v in recent_vols)

            if max_vol_ratio > 5.0:
                volume_score = 0  # 爆量>5倍 ⇒ 0分
            elif max_vol_ratio > 3.0:
                volume_score = 5   # 突破放量3-5倍 ⇒ 5分
            elif max_vol_ratio >= 1.0:
                volume_score = 10  # 维持量能1-3倍 ⇒ 10分
        scores['volume_score'] = volume_score

        # 6. 20日回撤 < 12%（10分）
        drawdown = latest.get('drawdown_20d', 0)
        drawdown_score = 0
        if drawdown >= -8:
            drawdown_score = 10
        elif drawdown >= -12:
            drawdown_score = 6
        scores['drawdown_score'] = drawdown_score

        # 总分
        total_score = sum(scores.values())

        # 趋势等级判断
        trend_level = 'E'
        if total_score >= 90:
            trend_level = 'S'
        elif total_score >= 80:
            trend_level = 'A'
        elif total_score >= 70:
            trend_level = 'B'
        elif total_score >= 60:
            trend_level = 'C'

        return {
            'total_score': total_score,
            'ma_score': ma_score,
            'gain_60d_score': gain_60d_score,
            'volume_score': volume_score,
            'recent_score': ma20_slope_score,
            'ma60_score': 0,
            'trend_level': trend_level,
            'trend_type': trend_level,
            'sector_score': sector_score,
            'drawdown_score': drawdown_score,
            'ma20_slope_score': ma20_slope_score,
            'reason': '正常'
        }
    
    def analyze_stock(self, stock_code: str) -> Optional[Dict]:
        """
        分析单只股票是否为趋势票
        
        参数：
        - stock_code: 股票代码
        
        返回：
        - Dict: 分析结果，失败返回None
        """
        try:
            # 获取K线数据
            df = self.fetch_stock_kline(stock_code, days=90)
            if df is None or len(df) < 20:
                return None

            # 计算技术指标
            df = self._calculate_indicators(df)

            # 查询stock_id
            stock_id = None
            try:
                conn = self._get_db_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT stock_id FROM stocks WHERE stock_code = ?', (stock_code,))
                result = cursor.fetchone()
                if result:
                    stock_id = result[0]
                conn.close()
            except:
                pass

            # 计算评分
            score_result = self._calculate_trend_score(df, stock_id)
            
            # 获取最新数据
            latest = df.iloc[-1]
            
            return {
                'stock_code': stock_code,
                'date': latest['date'],
                'price': latest['close'],
                'change_percent': latest['change_pct'],
                'volume_ratio': latest['volume_ratio'],
                'ma5': latest['ma5'],
                'ma10': latest['ma10'],
                'ma20': latest['ma20'],
                'ma60': latest.get('ma60', 0),
                'change_pct_60d': latest['change_pct_60d'],
                'drawdown_20d': latest['drawdown_20d'],
                **score_result
            }
            
        except Exception as e:
            logger.error(f"分析股票 {stock_code} 失败: {e}", exc_info=True)
            return None
    
    async def get_trend_stocks_by_date(self, trade_date: str, config: Dict = None) -> List[Dict]:
        """
        获取指定日期的趋势标的列表（异步版本）
        
        容错机制：
        1. 先通过强势股池进行严格初选
        2. 尝试获取精确分析（API调用）
        3. 如果API全部失败，返回初选的潜在股（标记为未精确分析）
         
        参数：
        - trade_date: 交易日期（格式：YYYY-MM-DD）
        - config: 自定义初选规则配置（字典），如果为None则使用默认值
         
        返回：
        - List[Dict]: 趋势标的列表（包含 is_approximate 标识）
        """
        try:
            # 设置默认配置
            def get_field_value(field_config, key, default):
                if field_config is None:
                    return default
                return field_config.get(key, default)

            if config is None:
                config = {
                    'price': {'enabled': True, 'min_enabled': True, 'max_enabled': False, 'min': 10.0, 'max': None},
                    'change_percent': {'enabled': True, 'min_enabled': True, 'max_enabled': False, 'min': 5.0, 'max': None},
                    'amount': {'enabled': True, 'min_enabled': True, 'max_enabled': False, 'min': 300.0, 'max': None},
                    'turnover': {'enabled': True, 'min_enabled': True, 'max_enabled': False, 'min': 2.0, 'max': None},
                    'volume_ratio': {'enabled': True, 'min_enabled': True, 'max_enabled': False, 'min': 1.0, 'max': None},
                    'continuous_limit': {'enabled': True, 'mode': 'require', 'min_days': 2},
                    'max_count': 20
                }

            # 提取配置
            price_config = config.get('price', {})
            change_percent_config = config.get('change_percent', {})
            amount_config = config.get('amount', {})
            turnover_config = config.get('turnover', {})
            volume_ratio_config = config.get('volume_ratio', {})
            continuous_limit_config = config.get('continuous_limit', {})
            max_count = int(config.get('max_count', 20))

            # 价格筛选
            price_enabled = get_field_value(price_config, 'enabled', True)
            price_min_enabled = get_field_value(price_config, 'min_enabled', True)
            price_max_enabled = get_field_value(price_config, 'max_enabled', False)
            price_min = get_field_value(price_config, 'min', 10.0)
            price_max = get_field_value(price_config, 'max', None)

            # 涨幅筛选
            change_percent_enabled = get_field_value(change_percent_config, 'enabled', True)
            change_percent_min_enabled = get_field_value(change_percent_config, 'min_enabled', True)
            change_percent_max_enabled = get_field_value(change_percent_config, 'max_enabled', False)
            change_percent_min = get_field_value(change_percent_config, 'min', 5.0)
            change_percent_max = get_field_value(change_percent_config, 'max', None)

            # 成交额筛选
            amount_enabled = get_field_value(amount_config, 'enabled', True)
            amount_min_enabled = get_field_value(amount_config, 'min_enabled', True)
            amount_max_enabled = get_field_value(amount_config, 'max_enabled', False)
            amount_min_wan = get_field_value(amount_config, 'min', 300.0)
            amount_max_wan = get_field_value(amount_config, 'max', None)

            # 换手率筛选
            turnover_enabled = get_field_value(turnover_config, 'enabled', True)
            turnover_min_enabled = get_field_value(turnover_config, 'min_enabled', True)
            turnover_max_enabled = get_field_value(turnover_config, 'max_enabled', False)
            turnover_min = get_field_value(turnover_config, 'min', 2.0)
            turnover_max = get_field_value(turnover_config, 'max', None)

            # 量比筛选
            volume_ratio_enabled = get_field_value(volume_ratio_config, 'enabled', True)
            volume_ratio_min_enabled = get_field_value(volume_ratio_config, 'min_enabled', True)
            volume_ratio_max_enabled = get_field_value(volume_ratio_config, 'max_enabled', False)
            volume_ratio_min = get_field_value(volume_ratio_config, 'min', 1.0)
            volume_ratio_max = get_field_value(volume_ratio_config, 'max', None)

            # 连板筛选
            continuous_limit_enabled = get_field_value(continuous_limit_config, 'enabled', True)
            continuous_limit_mode = get_field_value(continuous_limit_config, 'mode', 'require')
            continuous_limit_min_days = get_field_value(continuous_limit_config, 'min_days', 2)

            # 打印筛选规则日志
            logger.info(f"初选规则配置: 价格(启用={price_enabled}, 最小={price_min_enabled}, 最小值={price_min}, 最大={price_max_enabled}, 最大值={price_max}), "
                       f"涨幅(启用={change_percent_enabled}, 最小={change_percent_min_enabled}, 最小值={change_percent_min}, 最大={change_percent_max_enabled}, 最大值={change_percent_max}), "
                       f"成交额(启用={amount_enabled}, 最小={amount_min_enabled}, 最小值={amount_min_wan}万, 最大={amount_max_enabled}, 最大值={amount_max_wan}万), "
                       f"换手率(启用={turnover_enabled}, 最小={turnover_min_enabled}, 最小值={turnover_min}, 最大={turnover_max_enabled}, 最大值={turnover_max}), "
                       f"量比(启用={volume_ratio_enabled}, 最小={volume_ratio_min_enabled}, 最小值={volume_ratio_min}, 最大={volume_ratio_max_enabled}, 最大值={volume_ratio_max}), "
                       f"连板(启用={continuous_limit_enabled}, 模式={continuous_limit_mode}, 最小天数={continuous_limit_min_days}), 最大数量={max_count}")

            conn = self._get_db_connection()
            cursor = conn.cursor()

            # 第一步：严格初选（基于强势股现有字段）
            # 构建SQL查询
            conditions = ['ss.trade_date = ?']
            params = [trade_date]

            # 价格筛选
            if price_enabled:
                if price_min_enabled and price_min is not None and price_min > 0:
                    conditions.append('ss.price >= ?')
                    params.append(price_min)
                if price_max_enabled and price_max is not None and price_max > 0:
                    conditions.append('ss.price <= ?')
                    params.append(price_max)

            # 涨幅筛选
            if change_percent_enabled:
                if change_percent_min_enabled and change_percent_min is not None and change_percent_min > 0:
                    conditions.append('ss.change_percent >= ?')
                    params.append(change_percent_min)
                if change_percent_max_enabled and change_percent_max is not None and change_percent_max > 0:
                    conditions.append('ss.change_percent <= ?')
                    params.append(change_percent_max)

            # 成交额筛选（转换为元）
            if amount_enabled:
                if amount_min_enabled and amount_min_wan is not None and amount_min_wan > 0:
                    conditions.append('ss.amount >= ?')
                    params.append(amount_min_wan * 10000)
                if amount_max_enabled and amount_max_wan is not None and amount_max_wan > 0:
                    conditions.append('ss.amount <= ?')
                    params.append(amount_max_wan * 10000)

            # 换手率筛选
            if turnover_enabled:
                if turnover_min_enabled and turnover_min is not None and turnover_min > 0:
                    conditions.append('ss.turnover_rate >= ?')
                    params.append(turnover_min)
                if turnover_max_enabled and turnover_max is not None and turnover_max > 0:
                    conditions.append('ss.turnover_rate <= ?')
                    params.append(turnover_max)

            # 量比筛选
            if volume_ratio_enabled:
                if volume_ratio_min_enabled and volume_ratio_min is not None and volume_ratio_min > 0:
                    conditions.append('ss.volume_ratio >= ?')
                    params.append(volume_ratio_min)
                if volume_ratio_max_enabled and volume_ratio_max is not None and volume_ratio_max > 0:
                    conditions.append('ss.volume_ratio <= ?')
                    params.append(volume_ratio_max)

            # 连板筛选
            if continuous_limit_enabled:
                if continuous_limit_mode == 'require':
                    conditions.append('ss.continuous_limit_days >= ?')
                    params.append(continuous_limit_min_days)
                elif continuous_limit_mode == 'exclude':
                    conditions.append('ss.continuous_limit_days < ?')
                    params.append(continuous_limit_min_days)

            where_clause = ' AND '.join(conditions)
            order_limit = f'ORDER BY ss.amount DESC LIMIT {max_count}'

            sql = f'''
                SELECT s.stock_id, s.stock_code, s.stock_name, s.industry,
                       ss.change_percent, ss.amount, ss.turnover_rate,
                       ss.volume_ratio, ss.price, ss.is_new_high,
                       ss.continuous_limit_days, ss.sector
                FROM strong_stocks ss
                JOIN stocks s ON ss.stock_id = s.stock_id
                WHERE {where_clause}
                {order_limit}
            '''

            cursor.execute(sql, tuple(params))

            preliminary_stocks = cursor.fetchall()
            conn.close()

            if not preliminary_stocks or len(preliminary_stocks) == 0:
                logger.info(f"日期 {trade_date} 初选无符合条件的强势股")
                return []

            # 转换为字典格式，方便后续处理
            potential_stocks = []
            for stock in preliminary_stocks:
                potential_stocks.append({
                    'stock_id': stock[0],
                    'stock_code': stock[1],
                    'stock_name': stock[2],
                    'industry': stock[3],
                    'change_percent': stock[4],
                    'amount': stock[5],
                    'turnover_rate': stock[6],
                    'volume_ratio': stock[7],
                    'price': stock[8],
                    'is_new_high': stock[9],
                    'continuous_limit_days': stock[10],
                    'sector': stock[11]
                })

            logger.info(f"日期 {trade_date} 初选获得 {len(potential_stocks)} 只潜在趋势标")

            # 第二步：尝试获取精确分析（API调用）- 使用异步避免阻塞
            trend_stocks = []
            api_fetch_success_count = 0  # API调用成功计数（获取K线成功）
            consecutive_fail_count = 0  # 连续失败计数器

            for i, stock_info in enumerate(potential_stocks):
                stock_id = stock_info['stock_id']
                stock_code = stock_info['stock_code']
                stock_name = stock_info['stock_name']

                # 连续失败3次后，停止API调用，直接返回初选数据
                if consecutive_fail_count >= 3:
                    logger.warning(f"连续{consecutive_fail_count}次API调用失败，停止分析，返回初选潜在股（未精确分析）")
                    break

                logger.info(f"分析股票 {i+1}/{len(potential_stocks)}: {stock_code} {stock_name}")

                try:
                    # 在每只股票之间增加额外的延迟（进一步防封禁）
                    if i > 0:  # 第一只股票不需要额外延迟
                        await asyncio.sleep(2.0)  # 额外等待2秒

                    # 使用异步方式获取K线数据，避免阻塞事件循环
                    df = await self._run_in_thread(self.fetch_stock_kline, stock_code, 90)

                    if df is None or len(df) < 20:
                        logger.warning(f"股票 {stock_code} 获取K线失败或数据不足")
                        consecutive_fail_count += 1
                        continue

                    # API调用成功，获取到K线数据
                    api_fetch_success_count += 1
                    consecutive_fail_count = 0  # 成功后重置计数器

                    # 计算技术指标（同步，但很快）
                    df = self._calculate_indicators(df)

                    # 计算评分（同步，但很快）
                    score_result = self._calculate_trend_score(df, stock_id)

                    # 保存K线数据到数据库（同步I/O）
                    self.save_stock_daily_data(stock_code, df)

                    # 评分≥50分才入选趋势标列表
                    if score_result['total_score'] >= 50:
                        latest = df.iloc[-1]

                        # 安全转换numpy类型为Python原生类型
                        def safe_get(val, default=0):
                            if pd.isna(val):
                                return default
                            return float(val) if isinstance(val, (np.floating, float)) else int(val)

                        trend_stocks.append({
                            'stock_code': stock_code,
                            'stock_name': stock_name,
                            'industry': stock_info['industry'],
                            'change_percent': stock_info['change_percent'],
                            'total_score': score_result['total_score'],
                            'ma_score': score_result.get('ma_score', 0),
                            'gain_60d_score': score_result.get('gain_60d_score', 0),
                            'volume_score': score_result.get('volume_score', 0),
                            'recent_score': score_result.get('recent_score', 0),
                            'ma60_score': score_result.get('ma60_score', 0),
                            'sector_score': score_result.get('sector_score', 0),
                            'drawdown_score': score_result.get('drawdown_score', 0),
                            'ma5': safe_get(latest['ma5']),
                            'ma10': safe_get(latest['ma10']),
                            'ma20': safe_get(latest['ma20']),
                            'ma60': safe_get(latest.get('ma60', 0)),
                            'volume_ratio': safe_get(latest['volume_ratio']),
                            'change_pct_60d': safe_get(latest['change_pct_60d']),
                            'drawdown_20d': safe_get(latest['drawdown_20d']),
                            'trend_level': score_result.get('trend_level', ''),
                            'is_approximate': False,  # 精确分析
                            'amount': stock_info['amount'],
                            'turnover_rate': stock_info['turnover_rate'],
                            'sector': stock_info['sector']
                        })
                        logger.info(f"股票 {stock_code} 分析成功: 总分{score_result['total_score']}")
                    else:
                        logger.info(f"股票 {stock_code} 分析完成: 总分{score_result['total_score']}（<50分，未入选）")

                except Exception as e:
                    logger.warning(f"股票 {stock_code} 分析失败: {str(e)[:100]}")
                    consecutive_fail_count += 1
                    continue

            # 第三步：容错机制 - 如果API全部失败或提前终止，返回初选的潜在股
            if not trend_stocks and api_fetch_success_count == 0:
                logger.warning(f"API全部失败，返回{len(potential_stocks)}只初选潜在股（未精确分析）")

                # 基于初选数据生成近似分析
                for stock_info in potential_stocks:
                    trend_stocks.append({
                        'stock_code': stock_info['stock_code'],
                        'stock_name': stock_info['stock_name'],
                        'industry': stock_info['industry'],
                        'change_percent': stock_info['change_percent'],
                        'total_score': 0,  # 未知
                        'ma_score': 0,  # 未知
                        'gain_60d_score': 0,  # 未知
                        'volume_score': 0,  # 未知
                        'recent_score': 0,  # 未知
                        'ma60_score': 0,  # 未知
                        'sector_score': 0,  # 未知
                        'drawdown_score': 0,  # 未知
                        'ma5': 0,  # 未知
                        'ma10': 0,  # 未知
                        'ma20': 0,  # 未知
                        'ma60': 0,  # 未知
                        'volume_ratio': stock_info['volume_ratio'],
                        'change_pct_60d': 0,  # 未知
                        'drawdown_20d': 0,  # 未知
                        'trend_level': '待分析',
                        'is_approximate': True,  # 标记为近似分析
                        'amount': stock_info['amount'],
                        'turnover_rate': stock_info['turnover_rate'],
                        'sector': stock_info['sector']
                    })

            # 按总分降序排序
            trend_stocks.sort(key=lambda x: x['total_score'] if x['total_score'] > 0 else 0, reverse=True)

            # 统计日志
            if api_fetch_success_count > 0:
                # API有成功获取的情况
                selected_count = len(trend_stocks)
                if selected_count == 0:
                    logger.info(f"日期 {trade_date} 趋势标统计: API成功获取{api_fetch_success_count}只, 但均未达到入选标准（评分<50分）")
                else:
                    logger.info(f"日期 {trade_date} 趋势标统计: API成功获取{api_fetch_success_count}只, 入选趋势标{selected_count}只")
            else:
                # API全部失败，返回初选数据
                logger.info(f"日期 {trade_date} 趋势标统计: API全部失败, 近似分析{len(trend_stocks)}只")

            # 将结果保存到数据库
            if trend_stocks:
                self.save_trend_stocks(trade_date, trend_stocks)
                logger.info(f"已保存 {len(trend_stocks)} 只趋势标到数据库 (日期: {trade_date})")

            return trend_stocks

        except Exception as e:
            logger.error(f"获取日期 {trade_date} 的趋势标的失败: {e}", exc_info=True)
            return []

    # 保留同步版本，用于向后兼容
    def get_trend_stocks_by_date_sync(self, trade_date: str) -> List[Dict]:
        """同步版本的获取趋势标方法（已废弃）"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.get_trend_stocks_by_date(trade_date))
            return result
        finally:
            loop.close()



    
    def save_trend_stocks(self, trade_date: str, trend_stocks: List[Dict]) -> bool:
        """
        保存趋势票到数据库

        参数：
        - trade_date: 交易日期
        - trend_stocks: 趋势标的数据列表

        返回：
        - bool: 是否成功
        """
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            # 先删除该日期的旧数据
            cursor.execute('''
                DELETE FROM trend_stocks WHERE trade_date = ?
            ''', (trade_date,))

            # 批量插入新数据
            now = datetime.now().isoformat()
            for stock in trend_stocks:
                # 获取stock_id
                cursor.execute('''
                    SELECT stock_id FROM stocks WHERE stock_code = ?
                ''', (stock['stock_code'],))
                result = cursor.fetchone()

                if result:
                    stock_id = result[0]
                else:
                    # 如果股票不存在，跳过
                    logger.warning(f"股票 {stock['stock_code']} 不存在于stocks表，跳过")
                    continue

                # 确定趋势级别
                total_score = stock['total_score']
                if total_score >= 90:
                    trend_level = 'S'
                elif total_score >= 80:
                    trend_level = 'A'
                elif total_score >= 70:
                    trend_level = 'B'
                elif total_score >= 60:
                    trend_level = 'C'
                else:
                    trend_level = '其他'

                # 获取 is_approximate 标记
                is_approximate = 1 if stock.get('is_approximate') else 0

                cursor.execute('''
                    INSERT INTO trend_stocks (
                        stock_id, trade_date, total_score, ma_score, gain_60d_score,
                        volume_score, recent_score, ma60_score, ma5, ma10, ma20,
                        ma60, change_pct_60d, drawdown_20d, volume_ratio, trend_level,
                        is_approximate, created_at, sector_score, drawdown_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    stock_id, trade_date,
                    stock['total_score'], stock['ma_score'], stock['gain_60d_score'],
                    stock['volume_score'], stock['recent_score'], stock['ma60_score'],
                    stock['ma5'], stock['ma10'], stock['ma20'], stock['ma60'],
                    stock['change_pct_60d'], stock['drawdown_20d'], stock['volume_ratio'],
                    trend_level, is_approximate, now,
                    stock.get('sector_score', 0), stock.get('drawdown_score', 0)
                ))

            conn.commit()
            conn.close()

            logger.info(f"成功保存 {len(trend_stocks)} 只趋势票到数据库 (日期: {trade_date})")
            return True

        except Exception as e:
            logger.error(f"保存趋势票失败: {e}", exc_info=True)
            return False
    
    def get_saved_trend_stocks(self, trade_date: str) -> List[Dict]:
        """
        从数据库读取已存储的趋势票

        参数：
        - trade_date: 交易日期

        返回：
        - List[Dict]: 趋势标的数据列表
        """
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT
                    ts.id, ts.stock_id, ts.trade_date, ts.total_score,
                    ts.ma_score, ts.gain_60d_score, ts.volume_score,
                    ts.recent_score, ts.ma60_score, ts.ma5, ts.ma10, ts.ma20,
                    ts.ma60, ts.change_pct_60d, ts.drawdown_20d, ts.volume_ratio,
                    ts.trend_level, ts.is_approximate, ts.created_at,
                    s.stock_code, s.stock_name, s.industry,
                    ss.change_percent, ss.amount
                FROM trend_stocks ts
                JOIN stocks s ON ts.stock_id = s.stock_id
                LEFT JOIN strong_stocks ss ON ts.stock_id = ss.stock_id AND ts.trade_date = ss.trade_date
                WHERE ts.trade_date = ?
                ORDER BY ts.total_score DESC
            ''', (trade_date,))

            rows = cursor.fetchall()

            trend_stocks = []
            for row in rows:
                stock_id = row[1]
                trend_stocks.append({
                    'id': row[0],
                    'stock_id': stock_id,
                    'trade_date': row[2],
                    'total_score': row[3],
                    'ma_score': row[4],
                    'gain_60d_score': row[5],
                    'volume_score': row[6],
                    'recent_score': row[7],
                    'ma60_score': row[8],
                    'ma5': row[9],
                    'ma10': row[10],
                    'ma20': row[11],
                    'ma60': row[12],
                    'change_pct_60d': row[13],
                    'drawdown_20d': row[14],
                    'volume_ratio': row[15],
                    'trend_level': row[16],
                    'is_approximate': row[17] == 1,
                    'created_at': row[18],
                    'stock_code': row[19],
                    'stock_name': row[20],
                    'industry': row[21],
                    'change_percent': row[22],
                    'amount': row[23],
                    'topics': []
                })

            conn = self._get_db_connection()
            cursor = conn.cursor()

            for stock in trend_stocks:
                stock_id = stock['stock_id']
                cursor.execute('''
                    SELECT t.topic_name, tsr.date
                    FROM topic_stock_relations tsr
                    JOIN topics t ON tsr.topic_id = t.topic_id
                    WHERE tsr.stock_id = ?
                    AND tsr.date <= ?
                    AND tsr.is_active = 1
                    ORDER BY tsr.date DESC, tsr.create_time DESC
                    LIMIT 1
                ''', (stock_id, trade_date))

                result = cursor.fetchone()
                if result and result[0]:
                    stock['topics'] = [result[0]]
                else:
                    stock['topics'] = []

            conn.close()

            if not rows or len(rows) == 0:
                return []

            logger.info(f"从数据库读取到 {len(trend_stocks)} 只趋势票 (日期: {trade_date})")
            return trend_stocks

        except Exception as e:
            logger.error(f"读取趋势票失败: {e}", exc_info=True)
            return []

    def save_stock_daily_data(self, stock_code: str, df: pd.DataFrame) -> bool:
        """
        保存股票日K线数据到数据库
        
        参数：
        - stock_code: 股票代码
        - df: K线数据DataFrame
        
        返回：
        - bool: 是否成功
        """
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            # 获取stock_id
            cursor.execute('''
                SELECT stock_id FROM stocks WHERE stock_code = ?
            ''', (stock_code,))
            result = cursor.fetchone()
            
            if not result:
                logger.warning(f"股票 {stock_code} 不存在于stocks表，跳过")
                return False
            
            stock_id = result[0]
            now = datetime.now().isoformat()
            
            # 批量插入数据
            for _, row in df.iterrows():
                cursor.execute('''
                    INSERT OR REPLACE INTO stock_daily_data
                    (stock_id, trade_date, open, high, low, close, volume, amount, change_pct, turnover,
                     ma5, ma10, ma20, ma60, volume_ratio, change_pct_60d, drawdown_20d, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    stock_id, row['date'], row['open'], row['high'], row['low'], row['close'],
                    row['volume'], row.get('amount', 0), row['change_pct'], row.get('turnover', 0),
                    row['ma5'], row['ma10'], row['ma20'], row.get('ma60', 0), 
                    row['volume_ratio'], row.get('change_pct_60d', 0), row.get('drawdown_20d', 0), now
                ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"成功保存股票 {stock_code} 的 {len(df)} 日K线数据到数据库")
            return True
            
        except Exception as e:
            logger.error(f"保存K线数据失败: {e}", exc_info=True)
            return False
    
    def calculate_and_save_trend_stocks(self, trade_date: str) -> Tuple[int, str]:
        """
        计算并保存趋势票（收盘后执行）- 同步版本

        参数：
        - trade_date: 交易日期

        返回：
        - Tuple[int, str]: (保存的数量, 状态描述)
        """
        try:
            logger.info(f"开始计算趋势票: {trade_date}")

            # 计算趋势票（使用同步包装版本）
            import asyncio
            loop = asyncio.get_event_loop()
            trend_stocks = loop.run_until_complete(self.get_trend_stocks_by_date(trade_date))

            if not trend_stocks or len(trend_stocks) == 0:
                return 0, "无趋势票"

            count = len(trend_stocks)
            message = f"成功计算并保存 {count} 只趋势票"
            logger.info(message)

            return count, message

        except Exception as e:
            logger.error(f"计算趋势票失败: {e}", exc_info=True)
            return 0, f"计算失败: {str(e)}"

    async def calculate_and_save_trend_stocks_async(self, trade_date: str) -> Tuple[int, str]:
        """
        计算并保存趋势票（收盘后执行）- 异步版本

        参数：
        - trade_date: 交易日期

        返回：
        - Tuple[int, str]: (保存的数量, 状态描述)
        """
        try:
            logger.info(f"开始计算趋势票（异步）: {trade_date}")

            # 计算趋势票（使用异步版本）
            trend_stocks = await self.get_trend_stocks_by_date(trade_date)

            if not trend_stocks or len(trend_stocks) == 0:
                return 0, "无趋势票"

            count = len(trend_stocks)
            message = f"成功计算并保存 {count} 只趋势票"
            logger.info(message)

            return count, message

        except Exception as e:
            logger.error(f"计算趋势票失败: {e}", exc_info=True)
            return 0, f"计算失败: {str(e)}"

