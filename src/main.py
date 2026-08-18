
from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import asyncio
import io
import json
import logging
import os
import random
import sqlite3
import struct
import zipfile
from datetime import datetime, timedelta, time
from fastapi.responses import Response
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 网络/外部库改为可选依赖：缺失时仅记录警告，不阻止 server 启动
# (server 仍可服务本地数据库/页面，只有调用在线数据功能时才报错)
try:
    import akshare as ak  # type: ignore[assignment]
except ImportError:
    ak = None  # type: ignore[assignment]
    logger.warning("akshare 未安装, 在线行情/涨跌停抓取功能不可用")

try:
    import pandas as pd  # type: ignore[assignment]
except ImportError:
    pd = None  # type: ignore[assignment]
    logger.warning("pandas 未安装, 数据处理受限 (仅影响在线抓取)")

from db_operations import (
    RotationAnalysisDB,
    get_last_trading_day,
    get_latest_trading_date_from_db,
    fetch_and_save_trading_days,
    get_recent_trading_days,
    ensure_trading_day_exists,
    get_trading_days_between
)
from market_mood_calculator import MarketMoodCalculator
from data.database import DB_PATH

app = FastAPI(
    title="A股复盘工具",
    description="专业A股复盘分析系统 - AkShare数据源",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = RotationAnalysisDB()

@app.on_event("startup")
async def startup_event():
    """服务启动时执行初始化"""
    logger.info("服务启动中...")

    # 初始化人气榜数据源（新高榜单）- 包含"人气、热度"相关的数据源
    # 关联关系：
    # - 本代码初始化 popularity_sources 表数据
    # - 前端显示: dashboard.html 的 rank-tabs (新高榜tab)
    # - 前端子选项卡: renderPopularitySubTabs() 渲染数据源子选项卡
    # - 数据获取: data_acquisition.py fetch_and_save_popularity_ranking() 获取新高榜单数据
    # - 数据库操作: db_operations.py 的 popularity_sources 相关方法
    # 过滤说明：只初始化"新高榜"数据源（半年新高、一年新高、历史新高）
    for source_name in ['半年新高', '一年新高', '历史新高']:
        try:
            logger.info(f"创建人气榜数据源: {source_name}")
            description = f'AkShare 新高榜单 - {source_name}'
            now = datetime.now().isoformat()
            conn = db._get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO popularity_sources (source_name, description, sort_order, is_active, created_at, updated_at)
                VALUES (?, ?, 0, 1, ?, ?)
            ''', (source_name, description, now, now))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"初始化人气榜数据源失败 {source_name}: {e}")

    # 自动检测并刷新交易日历 (剩余 < 60 天时刷新)
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(date) FROM trading_days')
        row = cursor.fetchone()
        conn.close()
        latest_date_str = row[0] if row else None
        if latest_date_str:
            from datetime import datetime as _dt
            latest_date = _dt.strptime(latest_date_str, '%Y-%m-%d').date()
            days_until_expiry = (latest_date - _dt.now().date()).days
            if days_until_expiry < 60:
                logger.info(f"交易日历剩余 {days_until_expiry} 天, 自动刷新")
                saved, _ = fetch_and_save_trading_days()
                logger.info(f"自动刷新完成: 新增 {saved} 条")
            else:
                logger.info(f"交易日历充足 (剩余 {days_until_expiry} 天), 无需刷新")
        else:
            logger.warning("trading_days 表为空, 自动刷新")
            saved, _ = fetch_and_save_trading_days()
            logger.info(f"自动刷新完成: 新增 {saved} 条")
    except Exception as e:
        logger.error(f"自动检测交易日历失败: {e}", exc_info=True)

    logger.info("服务启动完成")


def is_in_trading_hours() -> bool:
    """判断当前是否在交易时段（9:25-15:00）

    条件：
    1. 必须是交易日
    2. 时间在 9:25 ~ 15:00 之间

    Returns:
        bool: True=在交易时段，False=不在交易时段
    """
    now = datetime.now()
    today = now.date().strftime("%Y-%m-%d")
    current_time = now.time()

    # 交易时段判断
    market_start = time(9, 25, 0)
    market_end = time(15, 0, 0)

    # 检查时间是否在交易时段
    if not (market_start <= current_time < market_end):
        logger.debug(f"当前时间 {current_time} 不在交易时段（9:25-15:00）")
        return False

    # 检查今天是否是交易日
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT is_active FROM trading_days
            WHERE date = ?
        ''', (today,))
        result = cursor.fetchone()
        conn.close()

        is_trading_day = result and result[0] == 1

        if is_trading_day:
            logger.debug(f"今天 {today} 是交易日，当前在交易时段")
        else:
            logger.debug(f"今天 {today} 不是交易日")

        return is_trading_day

    except Exception as e:
        logger.error(f"检查是否交易日失败: {e}")
        return False


def get_query_trading_date() -> str:
    """获取查询首板数据时应该使用的交易日

    用途说明：
    - 用于今日首板板块、题材轮动分析等需要开盘后查看最新数据的板块
    - 规则：9:15（开盘时间）前查上一交易日，9:15后查当天

    业务逻辑：
    - 9:15之前：查询上一个交易日（开盘前没有新数据）
    - 9:15及之后：如果当天是交易日，查询当天；否则查上一交易日

    返回日期的用途：
    - 查询首板数据（first_limits 表）
    - 查询题材卡片（topic_activations 表的 activation_date）
    - 查询首板-题材关联（first_limit_topics 表的 association_date）

    Returns:
        应该查询的交易日期字符串（格式：YYYY-MM-DD）
    """
    now = datetime.now()
    current_date = now.date().strftime("%Y-%m-%d")
    current_time = now.time()
    
    market_open_time = time(9, 15)
    
    # 从数据库获取最新交易日
    latest_trading_date = get_latest_trading_date_from_db()
    
    if latest_trading_date is None:
        # 数据库没有数据，使用最近的工作日
        return get_last_trading_day()
    
    # 如果当前时间在9:15之前，应该查询上一个交易日
    if current_time < market_open_time:
        # 查询今天之前的最新交易日（使用 <today 而不是 <=today）
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT MAX(date)
            FROM trading_days
            WHERE date < ? AND is_active = 1
        ''', (current_date,))
        result = cursor.fetchone()
        conn.close()

        if result and result[0]:
            return result[0]
        return latest_trading_date
    
    # 如果当前时间在9:15及之后
    # 检查数据库中最新交易日是否是今天
    if latest_trading_date != current_date:
        # 最新交易日不是今天，说明今天还没有交易数据
        # 可能为非交易日或刚开盘但还没数据，返回数据库中的最新交易日
        logger.info(f"数据库最新交易日({latest_trading_date})不是今天({current_date})，使用最新交易日")
        return latest_trading_date
    
    # 最新交易日是今天，且当前时间是9:15及之后，查询今天
    logger.info(f"查询今天的首板数据: {current_date}")
    return current_date


def get_display_trade_date(query_existing_data: bool = True) -> str:
    """获取当前应该展示的交易日期（用于涨跌停统计、连板梯队、题材轮动分析等）

    用途说明：
    - 用于涨跌停统计、连板梯队等需要在收盘后数据才固定的板块
    - 用于题材轮动分析等需要展示最近交易日信息的板块
    - 规则：15:00（收盘时间）前查上一交易日，15:00后查当天

    业务逻辑：
    - 15:00之前或非交易日：查询数据库中的最新交易日（数据未固定）
    - 15:00及之后且是工作日：
      - query_existing_data=True: 如果当天数据存在，返回当天；否则返回最新有数据的交易日
      - query_existing_data=False: 返回当天（即使没有数据），用于展示日期而非查询数据

    返回日期的用途：
    - 查询涨跌停统计（limit_stats 表的 trade_date）- query_existing_data=True
    - 查询连板梯队数据 - query_existing_data=True
    - 题材轮动分析（展示最近交易日）- query_existing_data=False
    - 注意：今日首板板块不应使用此函数，应使用 get_query_trading_date()

    Args:
        query_existing_data: 是否只返回有数据的交易日（True用于查询数据，False用于展示日期）

    Returns:
        应该展示的交易日期字符串（格式：YYYY-MM-DD）
    """
    now = datetime.now()
    current_date = now.date().strftime("%Y-%m-%d")
    current_time = now.time()
    weekday = now.weekday()

    market_close_time = time(15, 0)

    # 从数据库获取最新交易日
    latest_trading_date = get_latest_trading_date_from_db()

    # 如果数据库没有数据，返回最近的工作日
    if latest_trading_date is None:
        logger.info("数据库中没有交易日数据，返回最近工作日")
        return get_last_trading_day()

    # 如果当前时间 >= 15:00 且是工作日（周一到周五）
    if current_time >= market_close_time and weekday < 5:
        # 查询今天是否是交易日
        from src.db_operations import is_trading_day
        today_is_trading_day = is_trading_day(current_date)

        if today_is_trading_day:
            # 今天是交易日
            if query_existing_data:
                # 只返回有数据的日期
                if latest_trading_date == current_date:
                    logger.info(f"当前时间 >= 15:00，今天是交易日且有数据，展示当天: {current_date}")
                    return current_date
                else:
                    logger.info(f"当前时间 >= 15:00，今天是交易日但无数据，展示最新交易日: {latest_trading_date}")
                    return latest_trading_date
            else:
                # 返回今天的日期（即使没有数据）
                logger.info(f"当前时间 >= 15:00，今天是交易日，返回当天: {current_date}")
                return current_date
        else:
            # 今天不是交易日，返回最新有数据的交易日
            logger.info(f"当前时间 >= 15:00，今天非交易日，展示最新交易日: {latest_trading_date}")
            return latest_trading_date

    # 当前时间 < 15:00 或非交易日，展示最新交易日
    return latest_trading_date

os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("data", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# 初始化数据库
try:
    from data.database import init_database
    init_database()
    logger.info("数据库初始化成功")
except Exception as e:
    logger.error(f"数据库初始化失败: {e}")

# 创建数据库操作实例
db = RotationAnalysisDB()

class StockDataService:
    def __init__(self):
        self.cache_dir = "data/cache"
        os.makedirs(self.cache_dir, exist_ok=True)
        self.history_data = self._load_history_data()
        self.last_update = None
        
    def is_trading_time(self):
        now = datetime.now()
        weekday = now.weekday()
        if weekday >= 5:
            return False
        
        current_time = now.time()
        if time(9, 30) <= current_time <= time(11, 30):
            return True
        if time(13, 0) <= current_time <= time(15, 0):
            return True
        return False
    
    def _load_history_data(self):
        history_file = "data/history.json"
        if os.path.exists(history_file):
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        return data
            except Exception as e:
                logger.warning(f"加载历史数据失败: {e}")
        
        base_date = datetime.now() - timedelta(days=30)
        history = []
        for i in range(30):
            date = base_date + timedelta(days=i)
            first = random.randint(20, 50)
            history.append({
                "date": date.strftime("%Y-%m-%d"),
                "first_limit": first,
                "continuous_limit": random.randint(10, int(first*0.6)),
                "exploded": random.randint(3, int(first*0.25)),
                "limit_down": random.randint(2, 20)
            })
        return history
    
    def _save_history_data(self):
        try:
            with open("data/history.json", 'w', encoding='utf-8') as f:
                json.dump(self.history_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存历史数据失败: {e}")
    
    def _get_cached_data(self, cache_key):
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        if os.path.exists(cache_file):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
                if (datetime.now() - mtime).total_seconds() < 300:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
            except Exception as e:
                logger.warning(f"读取缓存失败: {e}")
        return None
    
    def _save_cached_data(self, cache_key, data):
        try:
            cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存缓存失败: {e}")
    
    def _calculate_median(self, data_list: List[int], n: int = 10) -> float:
        if not data_list:
            return 0
        recent_data = data_list[-n:] if len(data_list) >= n else data_list
        sorted_data = sorted(recent_data)
        length = len(sorted_data)
        if length == 0:
            return 0
        mid = length // 2
        if length % 2 == 0:
            return (sorted_data[mid-1] + sorted_data[mid]) / 2
        return float(sorted_data[mid])
    
    def _get_market_mood(self, first_limit: int, median_first: float) -> str:
        if not median_first or median_first == 0:
            return "正常"
        ratio = first_limit / median_first if median_first > 0 else 1
        if ratio >= 1.5:
            return "狂热"
        elif ratio >= 1.2:
            return "活跃"
        elif ratio >= 0.8:
            return "正常"
        elif ratio >= 0.5:
            return "谨慎"
        return "低迷"
    
    def _get_realtime_data(self):
        if ak is None or pd is None:
            logger.debug("akshare/pandas 未安装, 跳过实时行情抓取")
            return None
        try:
            df = ak.stock_zh_a_spot_em()
            if df is not None and len(df) > 0:
                # pandas: pd.to_numeric returns Series for column inputs, fillna returns Series
                df['涨跌幅'] = pd.to_numeric(df['涨跌幅'], errors='coerce').fillna(0).astype(float)
                df['成交额'] = pd.to_numeric(df['成交额'], errors='coerce').fillna(0).astype(float)
                df['最新价'] = pd.to_numeric(df['最新价'], errors='coerce').fillna(0).astype(float)
                df['今开'] = pd.to_numeric(df['今开'], errors='coerce').fillna(0).astype(float)
                df['昨收'] = pd.to_numeric(df['昨收'], errors='coerce').fillna(0).astype(float)
                return df
        except Exception as e:
            logger.warning(f"获取实时数据异常: {e}")
        return None
    
    async def get_limit_stats(self, query_date: str = None) -> Dict:
        """获取涨跌停统计数据（从数据库读取）

        Args:
            query_date: 查询日期，格式：YYYY-MM-DD，如果不提供则使用 get_display_trade_date()

        Returns:
            {
                "display_date": "2025-01-31",          # 当前展示的数据日期
                "is_current_day": False,               # 是否为当天（收盘后才为True）
                "source": "数据库/实时",               # 数据来源
                "current": {
                    "date": "2025-01-31",
                    "first_limit": 30,
                    "continuous_limit": 15,
                    "exploded": 5,
                    "limit_down": 8,
                    "explode_rate": 11.11,
                    "market_mood": 4,
                    "market_mood_text": "活跃"
                },
                "previous": {
                    "date": "2025-01-30",
                    "first_limit": 25,
                    ...
                },
                "change": {...},
                "median": {...},
                "analysis": "...",
                "history": [...]
            }
        """
        try:
            from data.database import MARKET_MOOD_MAP

            # 确定查询日期
            if query_date:
                # 如果用户指定了查询日期，直接使用
                display_date = query_date
            else:
                # 否则根据当前时间和交易日规则计算查询日期
                now = datetime.now()
                current_date = now.date().strftime("%Y-%m-%d")
                current_time = now.time()
                market_close_time = time(15, 0)

                # 检查今天是否是交易日
                conn = db._get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT date FROM trading_days
                    WHERE date = ? AND is_active = 1
                ''', (current_date,))
                is_trading_day = cursor.fetchone() is not None
                conn.close()

                if is_trading_day:
                    # 今天是交易日
                    if current_time >= market_close_time:
                        # 15:00之后，查询今天
                        display_date = current_date
                    else:
                        # 15:00之前，查询上一个交易日
                        display_date = db.get_previous_trading_day(current_date)
                else:
                    # 今天不是交易日，查询上一个交易日
                    display_date = db.get_previous_trading_day(current_date)

            # 从 trading_days 表获取24个交易日列表（从旧到新）
            trading_days_list = db.get_trading_days_backwards_from_date(display_date, 24)

            if not trading_days_list:
                logger.warning(f"无法获取交易日列表")
                return await self._get_fallback_limit_stats_db_mode()

            # 初始化市场情绪计算器（用于计算历史数据）
            calculator = None
            try:
                db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fupan.db")
                if os.path.exists(db_path):
                    calculator = MarketMoodCalculator(db_path)
            except Exception as e:
                logger.warning(f"初始化市场情绪计算器失败: {e}")

            # 构建历史数据：对每个交易日查询数据，没有数据的日期填充null
            history = []
            for date in trading_days_list:
                stats = db.get_limit_stats_by_date(date)
                if stats:
                    # 计算新情绪系统分数
                    mood_score = None
                    mood_name = None
                    if calculator:
                        try:
                            mood_result = calculator.calculate_market_mood(date)
                            if mood_result['success']:
                                mood_score = mood_result['total_score']
                                mood_name = mood_result['mood_name']
                                # 注意：不再在后端 clip mood_score。前端 moodChart 渲染时会在 Y 轴范围 clip 到 -40，
                                # 但 points[].score（用于 tooltip 显示）保持真实值，不丢失信息
                        except Exception as e:
                            logger.warning(f"计算{date}情绪分数失败: {e}")

                    history.append({
                        'date': stats['trade_date'],
                        'first_limit': stats['first_limit'],
                        'continuous_limit': stats['continuous_limit'],
                        'exploded': stats['exploded'],
                        'limit_down': stats['limit_down'],
                        'explode_rate': stats['explode_rate'],
                        'market_mood': stats['market_mood'],
                        'mood_score': mood_score,
                        'mood_name': mood_name
                    })
                else:
                    # 没有数据的日期，填充null值
                    history.append({
                        'date': date,
                        'first_limit': None,
                        'continuous_limit': None,
                        'exploded': None,
                        'limit_down': None,
                        'explode_rate': None,
                        'market_mood': None,
                        'mood_score': None,
                        'mood_name': None
                    })

            # 关闭计算器
            if calculator:
                calculator.close()

            # 当前日期的数据（用于显示在首板、炸板、跌停处）
            current_stats = db.get_limit_stats_by_date(display_date)

            # 构建current数据: 如果有数据就使用实际数据，没有数据就显示null的占位符
            if current_stats:
                current_data = {
                    "date": current_stats['trade_date'],
                    "first_limit": current_stats['first_limit'],
                    "continuous_limit": current_stats['continuous_limit'],
                    "exploded": current_stats['exploded'],
                    "limit_down": current_stats['limit_down'],
                    "explode_rate": round(current_stats['explode_rate'], 2),
                    "market_mood": current_stats['market_mood'],
                    "market_mood_text": current_stats['market_mood_text']
                }

                # 计算前一个交易日的数据
                previous_stats = db.get_previous_limit_stats(display_date)

                if previous_stats:
                    change_data = {
                        "first_limit": current_stats['first_limit'] - previous_stats['first_limit'],
                        "continuous_limit": current_stats['continuous_limit'] - previous_stats['continuous_limit'],
                        "exploded": current_stats['exploded'] - previous_stats['exploded'],
                        "limit_down": current_stats['limit_down'] - previous_stats['limit_down']
                    }
                else:
                    change_data = {
                        "first_limit": 0,
                        "continuous_limit": 0,
                        "exploded": 0,
                        "limit_down": 0
                    }

                previous_data = {
                    "date": previous_stats['trade_date'] if previous_stats else display_date,
                    "first_limit": previous_stats['first_limit'] if previous_stats else 0,
                    "continuous_limit": previous_stats['continuous_limit'] if previous_stats else 0,
                    "exploded": previous_stats['exploded'] if previous_stats else 0,
                    "limit_down": previous_stats['limit_down'] if previous_stats else 0,
                    "explode_rate": round(previous_stats['explode_rate'], 2) if previous_stats else 0,
                    "market_mood": previous_stats['market_mood'] if previous_stats else 3,
                    "market_mood_text": previous_stats['market_mood_text'] if previous_stats else "正常"
                } if previous_stats else None
            else:
                current_data = {
                    "date": display_date,
                    "first_limit": None,
                    "continuous_limit": None,
                    "exploded": None,
                    "limit_down": None,
                    "explode_rate": None,
                    "market_mood": None,
                    "market_mood_text": "无数据"
                }
                change_data = {
                    "first_limit": 0,
                    "continuous_limit": 0,
                    "exploded": 0,
                    "limit_down": 0
                }
                previous_data = None

            # 计算中位数
            median_data = {
                "first_limit": db.calculate_limit_stats_median('first_limit', 10),
                "continuous_limit": db.calculate_limit_stats_median('continuous_limit', 10),
                "exploded": db.calculate_limit_stats_median('exploded', 10),
                "limit_down": db.calculate_limit_stats_median('limit_down', 10)
            }

            # 获取分析文本
            analysis_data = db.get_limit_analysis(display_date)
            analysis_text = analysis_data['analysis'] if analysis_data else ""

            # 计算新的市场情绪（使用 MarketMoodCalculator）
            market_mood_result = None
            try:
                db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fupan.db")
                if os.path.exists(db_path):
                    calculator = MarketMoodCalculator(db_path)
                    calculator_result = calculator.calculate_market_mood(display_date)
                    if calculator_result['success']:
                        market_mood_result = {
                            'total_score': calculator_result['total_score'],
                            'mood_level': calculator_result['mood_level'],
                            'mood_name': calculator_result['mood_name'],
                            'indicator_scores': calculator_result.get('indicator_scores', {}),
                            'indicator_details': calculator_result.get('indicator_details', {})
                        }
                    calculator.close()
            except Exception as e:
                logger.warning(f"计算市场情绪失败: {e}")
                market_mood_result = None

            current_date = datetime.now().strftime("%Y-%m-%d")
            is_current_day = (display_date == current_date)
            now = datetime.now()
            source = "数据库"
            if is_current_day and now.time() >= time(15, 0):
                source = "实时（已收盘）"

            result = {
                "timestamp": datetime.now().isoformat(),
                "display_date": display_date,
                "is_current_day": is_current_day,
                "source": source,
                "current": current_data,
                "previous": previous_data,
                "change": change_data,
                "median": {
                    "first_limit": round(median_data['first_limit'], 1),
                    "continuous_limit": round(median_data['continuous_limit'], 1),
                    "exploded": round(median_data['exploded'], 1),
                    "limit_down": round(median_data['limit_down'], 1)
                },
                "history": history,
                "analysis": analysis_text,
                "market_mood": market_mood_result
            }

            self._save_cached_data("limit_stats", result)
            return result

        except Exception as e:
            logger.error(f"获取涨跌停数据失败: {e}", exc_info=True)
            return await self._get_fallback_limit_stats_db_mode()
    
    async def _get_fallback_limit_stats_db_mode(self) -> Dict:
        """备用数据返回（数据库模式的新结构）"""
        from data.database import MARKET_MOOD_MAP

        display_date = get_last_trading_day()

        today_first = random.randint(22, 48)
        today_continuous = random.randint(8, int(today_first*0.65))
        today_exploded = random.randint(4, int(today_first*0.28))
        today_limit_down = random.randint(3, 18)

        total_for_rate = today_exploded + today_first + today_continuous
        explode_rate = (today_exploded / total_for_rate * 100) if total_for_rate > 0 else 0
        market_mood_value = random.randint(1, 5)

        return {
            "timestamp": datetime.now().isoformat(),
            "display_date": display_date,
            "is_current_day": False,
            "source": "备用数据",
            "current": {
                "date": display_date,
                "first_limit": today_first,
                "continuous_limit": today_continuous,
                "exploded": today_exploded,
                "limit_down": today_limit_down,
                "explode_rate": round(explode_rate, 2),
                "market_mood": market_mood_value,
                "market_mood_text": MARKET_MOOD_MAP.get(market_mood_value, "未知")
            },
            "previous": {
                "date": display_date,
                "first_limit": today_first - random.randint(-5, 5),
                "continuous_limit": today_continuous - random.randint(-3, 3),
                "exploded": today_exploded - random.randint(-2, 2),
                "limit_down": today_limit_down - random.randint(-3, 3),
                "explode_rate": 0,
                "market_mood": 3,
                "market_mood_text": "正常"
            },
            "change": {
                "first_limit": random.randint(-5, 5),
                "continuous_limit": random.randint(-3, 3),
                "exploded": random.randint(-2, 2),
                "limit_down": random.randint(-3, 3)
            },
            "median": {
                "first_limit": 30.0,
                "continuous_limit": 18.0,
                "exploded": 6.0,
                "limit_down": 10.0
            },
            "history": self.history_data,
            "analysis": "当前使用备用数据源，暂无数据库数据。"
        }

    async def _get_fallback_limit_stats(self) -> Dict:
        """兼容旧版API的备用数据（已废弃，由_get_fallback_limit_stats_db_mode替代）"""
        return await self._get_fallback_limit_stats_db_mode()

    async def get_continuous_limits(self, query_date: str = None) -> List[Dict]:
        """获取连板榜数据

        Args:
            query_date: 查询日期，格式：YYYY-MM-DD，如果不提供则使用实时数据

        Returns:
            List[Dict]: 连板数据列表
        """
        try:
            # 如果提供了查询日期，从数据库读取历史数据
            if query_date:
                return db.get_continuous_limits_by_date(query_date)

            # 否则使用实时数据
            cached = self._get_cached_data("continuous_limits")
            if cached and not self.is_trading_time():
                return cached

            df = self._get_realtime_data()

            if df is None or len(df) < 10:
                return await self._get_fallback_continuous_limits()

            limit_up = df[df['涨跌幅'] >= 9.9].copy()
            
            continuous_limits = []
            idx = 0
            
            for _, row in limit_up.iterrows():
                if idx >= 12:
                    break
                
                price = row.get('最新价', 0)
                if price is None or (isinstance(price, float) and price != price) or price == 0:
                    continue

                code = str(row.get('代码', ''))
                name = str(row.get('名称', ''))
                change = float(row.get('涨跌幅', 0))
                trade_amount = row.get('成交额', 0)
                amount = (float(trade_amount) / 100000000
                          if (trade_amount is not None
                              and not (isinstance(trade_amount, float) and trade_amount != trade_amount))
                          else 0)
                
                days = random.choice([2, 2, 2, 3, 3, 4, 5])
                
                continuous_limits.append({
                    "code": code,
                    "name": name,
                    "price": round(float(price), 2),
                    "change_percent": round(change, 2),
                    "continuous_days": days,
                    "reason": "持续强势",
                    "sector": "市场热点",
                    "amount": round(amount, 1)
                })
                idx += 1
            
            continuous_limits.sort(key=lambda x: x["continuous_days"], reverse=True)
            result = continuous_limits[:10]
            
            self._save_cached_data("continuous_limits", result)
            return result
            
        except Exception as e:
            logger.error(f"获取连板数据失败: {e}", exc_info=True)
            return await self._get_fallback_continuous_limits()
    
    async def _get_fallback_continuous_limits(self) -> List[Dict]:
        hot_stocks = [
            ("600000", "浦发银行", 8.45, "银行"),
            ("601398", "工商银行", 5.23, "银行"),
            ("000001", "平安银行", 12.34, "银行"),
            ("002594", "比亚迪", 245.80, "新能源汽车"),
            ("300750", "宁德时代", 185.60, "新能源"),
            ("000858", "五粮液", 156.78, "白酒"),
            ("600519", "贵州茅台", 1650.00, "白酒"),
            ("000333", "美的集团", 62.34, "家电"),
            ("000002", "万科A", 18.56, "房地产"),
            ("600176", "东方通信", 8.56, "通信"),
        ]
        
        continuous_limits = []
        for code, name, price, sector in hot_stocks:
            days = random.choice([2, 2, 2, 3, 3, 4, 5])
            continuous_limits.append({
                "code": code,
                "name": name,
                "price": price,
                "change_percent": round(random.uniform(9.95, 10.05), 2),
                "continuous_days": days,
                "reason": "持续强势",
                "sector": sector,
                "amount": round(random.uniform(5, 35), 1)
            })
        
        continuous_limits.sort(key=lambda x: x["continuous_days"], reverse=True)
        return continuous_limits[:10]
    
    async def get_continuous_limit_analysis(self, query_date: str = None) -> Dict:
        """获取连板梯队分析（从数据库读取）

        Args:
            query_date: 查询日期，格式：YYYY-MM-DD，如果不提供则使用 get_display_trade_date()

        Returns:
            {
                "trade_date": "2025-01-31",
                "analysis": "分析内容...",
                "update_time": "2025-01-31T16:00:00",
                "limits_data": [...]  # 连板历史数据列表
            }
        """
        try:
            if query_date is None:
                query_date = get_display_trade_date()

            # 查询分析文本
            analysis_data = db.get_continuous_limits_analysis(query_date)
            if analysis_data:
                analysis_text = analysis_data.get('analysis', '')
                update_time = analysis_data.get('update_time', '')
            else:
                analysis_text = ''
                update_time = ''

            # 查询历史数据
            limits_history = db.get_continuous_limits_by_date(query_date)

            # 查询连板数量（从 limit_stats 表获取准确的连板数）
            limit_stats_data = db.get_limit_stats_by_date(query_date)
            continuous_limit_count = limit_stats_data.get('continuous_limit', 0) if limit_stats_data else len([x for x in limits_history if x.get('continuous_days', 0) >= 2])

            result = {
                "trade_date": query_date,
                "analysis": analysis_text,
                "update_time": update_time,
                "limits_data": limits_history,
                "continuous_limit_count": continuous_limit_count
            }
            return result

        except Exception as e:
            logger.error(f"获取连板分析失败: {e}", exc_info=True)
            return {
                "trade_date": query_date or get_display_trade_date(),
                "analysis": "",
                "update_time": "",
                "limits_data": [],
                "continuous_limit_count": 0,
                "error": str(e)
            }
    
    async def get_sector_analysis(self) -> Dict:
        try:
            cached = self._get_cached_data("sectors")
            if cached and not self.is_trading_time():
                return cached

            df_sector = None
            if ak is None:
                logger.debug("akshare 未安装, 跳过板块实时抓取")
            else:
                try:
                    df_sector = ak.stock_sector_spot()
                except Exception as e:
                    logger.warning(f"获取板块数据失败: {e}")

            if df_sector is not None and len(df_sector) > 0:
                sector_list = []
                for _, row in df_sector.head(15).iterrows():
                    sector_list.append({
                        "name": str(row.get('板块', row.get('label', '')))[:8],
                        "change_percent": round(float(row.get('个股-涨跌幅', 0)), 2),
                        "limit_up_count": 0,
                        "hot": float(row.get('个股-涨跌幅', 0)) >= 2,
                        "days": 1
                    })

                sector_list.sort(key=lambda x: x["change_percent"], reverse=True)
                hot_sectors = [s for s in sector_list if s["hot"]]

                # 加载保存的题材轮动分析数据
                rotation_data = self._load_rotation_analysis()

                result = {
                    "timestamp": datetime.now().isoformat(),
                    "sectors": sector_list,
                    "hot_sectors": hot_sectors[:8],
                    "sector_rotation": rotation_data
                }

                self._save_cached_data("sectors", result)
                return result

            fallback_data = await self._get_fallback_sector_analysis()
            # 加载保存的题材轮动分析数据
            rotation_data = self._load_rotation_analysis()
            fallback_data["sector_rotation"] = rotation_data
            return fallback_data

        except Exception as e:
            logger.error(f"获取题材数据失败: {e}", exc_info=True)
            return {"timestamp": datetime.now().isoformat(), "sectors": [], "hot_sectors": [], "sector_rotation": []}
    
    def _load_rotation_analysis(self) -> List[Dict]:
        """从数据库加载题材轮动分析数据

        核心逻辑：
        1. 从交易日表获取最近5个交易日期
        2. 基于这些日期查询题材数据
        3. 即使某天没有题材数据也能正常显示
        """
        try:
            # 使用应该展示的交易日期作为基准（凌晨时为上一个交易日）
            # 注意：题材轮动分析需要展示最近交易日，即使没有数据也要展示日期
            display_trade_date = get_display_trade_date(query_existing_data=False)
            display_date_obj = datetime.strptime(display_trade_date, "%Y-%m-%d").date()

            # 从交易日表获取最近5个交易日（基于当前应该展示的交易日期）
            recent_trading_dates = get_recent_trading_days(5, display_trade_date)

            if not recent_trading_dates:
                logger.warning("无法获取交易日，返回默认数据")
                return self._get_default_rotation_data()

            # 查询这些交易日的题材数据
            conn = db._get_connection()
            cursor = conn.cursor()
            
            # 获取所有题材
            cursor.execute('SELECT topic_id, topic_name FROM topics WHERE is_active = 1')
            all_topics = cursor.fetchall()
            
            # 查询这些日期的题材分析数据
            topics_dict = {}
            
            for topic_id, topic_name in all_topics:
                topics_dict[topic_name] = {
                    'name': topic_name,
                    'days': {},
                    'stages': {},
                    'hot': False
                }

            # 查询最近5个交易日的数据
            placeholders = ','.join(['?'] * len(recent_trading_dates))
            cursor.execute(f'''
                SELECT t.topic_name, ra.date, ra.content, ra.is_active, ra.stage
                FROM rotation_actives ra
                JOIN topics t ON ra.topic_id = t.topic_id
                WHERE ra.date IN ({placeholders})
                ORDER BY t.topic_name, ra.date
            ''', tuple(recent_trading_dates))

            records = cursor.fetchall()
            conn.close()

            # 填充数据到 topics_dict
            for topic_name, date_str, content, is_active, stage in records:
                if topic_name not in topics_dict:
                    continue

                # 计算相对天数（给前端用），相对于应该展示的交易日期
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                day_diff = int((date_obj - display_date_obj).days)

                topics_dict[topic_name]['days'][str(day_diff)] = content or ''
                topics_dict[topic_name]['stages'][str(day_diff)] = stage

                if is_active == 1 and content and content.strip():
                    topics_dict[topic_name]['hot'] = True
            
            # 确保包含所有交易日（即使没有数据）
            for topic_data in topics_dict.values():
                for date_str in recent_trading_dates:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                    day_diff = int((date_obj - display_date_obj).days)
                    
                    day_str = str(day_diff)
                    if day_str not in topic_data['days']:
                        topic_data['days'][day_str] = ''
            
            # 转换为列表并按日期排序
            topics_result = []
            for topic_data in topics_dict.values():
                # 获取day值的列表并排序（从早到晚）
                day_keys = sorted([int(d) for d in topic_data['days'].keys()])

                # 创建有序的days字典和stages字典
                ordered_days = {}
                ordered_stages = {}
                for day in day_keys:
                    ordered_days[str(day)] = topic_data['days'][str(day)]
                    if 'stages' in topic_data and str(day) in topic_data['stages']:
                        ordered_stages[str(day)] = topic_data['stages'][str(day)]

                topics_result.append({
                    'name': topic_data['name'],
                    'days': ordered_days,
                    'stages': ordered_stages,
                    'hot': topic_data['hot']
                })
            
            # 按最近一天是否有内容排序
            if day_keys:
                latest_day = str(day_keys[-1])
                topics_result.sort(key=lambda x: len(x['days'][latest_day]) if latest_day in x['days'] else 0, reverse=True)

            return topics_result

        except Exception as e:
            logger.warning(f"加载题材轮动分析失败: {e}", exc_info=True)
            return self._get_default_rotation_data()
    
    def _get_default_rotation_data(self) -> List[Dict]:
        """获取默认的题材数据（01-22到01-31）"""
        default_topics = [
            {
                "name": "人工智能",
                "days": {
                    "-9": "AI大模型应用落地加速，个股表现活跃",
                    "-8": "多只概念股涨停，板块热度攀升",
                    "-7": "板块轮动加速，资金持续流入",
                    "-6": "出现分化调整，部分个股冲高回落",
                    "-5": "早盘逆势拉升，但后劲不足",
                    "-4": "板块整体震荡整理，等待新催化",
                    "-3": "午后突然拉升，资金回流迹象明显",
                    "-2": "与指数共振上涨，多股封板",
                    "-1": "指数下跌时逆势上扬，多只个股走强"
                },
                "hot": True
            },
            {
                "name": "半导体",
                "days": {
                    "-9": "国产替代概念升温，资金关注度高",
                    "-8": "板块表现强势，多股冲击涨停",
                    "-7": "技术突破消息刺激，板块全线上涨",
                    "-6": "高位震荡，分歧加大",
                    "-5": "获利盘回吐，板块跌幅居前",
                    "-4": "探底回升，显示支撑较强",
                    "-3": "跟随指数反弹，但量能不足",
                    "-2": "震荡加剧，关注后续走势",
                    "-1": "缩量调整，获利盘有所出逃"
                },
                "hot": False
            },
            {
                "name": "新能源",
                "days": {
                    "-9": "",
                    "-8": "利好消息刺激，板块快速拉升",
                    "-7": "板块联动上涨，多股涨停",
                    "-6": "高位震荡，分歧开始显现",
                    "-5": "获利盘出逃，板块大幅回调",
                    "-4": "止跌企稳，低位承接良好",
                    "-3": "弱势震荡，等待新方向",
                    "-2": "",
                    "-1": "午后有异动，但持续性不佳"
                },
                "hot": False
            },
            {
                "name": "机器人",
                "days": {
                    "-9": "产业政策利好，板块启动",
                    "-8": "",
                    "-7": "",
                    "-6": "",
                    "-5": "龙头股涨停，带动板块走强",
                    "-4": "板块分化，轮动加快",
                    "-3": "",
                    "-2": "午后和指数共振拉升，但是上板标的不多",
                    "-1": "跟随指数调整，等待机会"
                },
                "hot": False
            },
            {
                "name": "光伏",
                "days": {
                    "-9": "",
                    "-8": "",
                    "-7": "",
                    "-6": "",
                    "-5": "",
                    "-4": "",
                    "-3": "",
                    "-2": "开盘逆势拉升，近期人气标的在开盘做出逆势拉升",
                    "-1": ""
                },
                "hot": False
            },
            {
                "name": "苏超",
                "days": {
                    "-9": "",
                    "-8": "",
                    "-7": "",
                    "-6": "",
                    "-5": "",
                    "-4": "",
                    "-3": "",
                    "-2": "",
                    "-1": ""
                },
                "hot": False
            }
        ]
        return default_topics
    
    async def _get_fallback_sector_analysis(self) -> Dict:
        all_sectors = [
            ("银行", 2.8, 6),
            ("人工智能", 4.3, 4),
            ("新能源汽车", 3.2, 3),
            ("半导体", 3.5, 3),
            ("白酒", 2.1, 3),
            ("光伏", 3.8, 2),
            ("通信", 2.9, 2),
            ("房地产", 1.5, 2),
        ]
        
        sector_list = []
        for name, change, limit_up in all_sectors:
            sector_list.append({
                "name": name,
                "change_percent": change,
                "limit_up_count": limit_up,
                "hot": change >= 2.5,
                "days": 1
            })
        
        sector_list.sort(key=lambda x: x["change_percent"], reverse=True)
        
        hot_sectors = [s for s in sector_list if s["hot"]]
        
        return {
            "timestamp": datetime.now().isoformat(),
            "sectors": sector_list,
            "hot_sectors": hot_sectors[:6]
        }

    async def get_first_limits(self) -> List[Dict]:
        """从数据库获取今日首板数据"""
        try:
            # 根据当前时间判断应该查询哪个交易日
            # 在9:15之前或非交易日，查询上一个交易日
            # 在9:15及之后的交易日，查询当天
            trading_date = get_query_trading_date()

            logger.info(f"查询首板数据，交易日：{trading_date}")
            records = db.get_first_limits_by_date(trading_date)

            first_limits = []
            for record in records:
                first_limits.append({
                    'code': record['code'],
                    'name': record['name'],
                    'price': record['price'],
                    'change_percent': 10.0 if record['limit_type'] == '10%' else (20.0 if record['limit_type'] == '20%' else 30.0),
                    'first_time': record['first_time'] or '09:30',
                    'sector': record['sector'] or '综合',
                    'reason': record['reason'],
                    'stock_id': record['stock_id'],
                    'id': record['id']
                })

            first_limits.sort(key=lambda x: x['first_time'])
            return first_limits[:12]

        except Exception as e:
            logger.error(f"获取首板数据失败: {e}", exc_info=True)
            return await self._get_fallback_first_limits()
    
    async def _get_fallback_first_limits(self) -> List[Dict]:
        first_stocks = [
            ("600000", "浦发银行", 8.45, "银行"),
            ("601398", "工商银行", 5.23, "银行"),
            ("601288", "农业银行", 3.45, "银行"),
            ("601166", "兴业银行", 18.90, "银行"),
            ("601939", "建设银行", 6.78, "银行"),
            ("002142", "宁波银行", 24.56, "银行"),
            ("601818", "光大银行", 3.78, "银行"),
            ("601229", "上海银行", 7.89, "银行"),
        ]
        
        num_stocks = random.randint(6, 10)
        selected = random.sample(first_stocks, min(num_stocks, len(first_stocks)))
        
        first_limits = []
        base_time = datetime.strptime("09:30", "%H:%M")
        
        for i, (code, name, price, sector) in enumerate(selected):
            minutes_offset = i * random.randint(2, 5)
            time_str = (base_time + timedelta(minutes=minutes_offset)).strftime("%H:%M")
            
            first_limits.append({
                "code": code,
                "name": name,
                "price": round(price + random.uniform(0, 0.3), 2),
                "change_percent": round(random.uniform(9.95, 10.1), 2),
                "first_time": time_str,
                "sector": sector,
                "reason": "首板涨停"
            })
        
        first_limits.sort(key=lambda x: x["first_time"])
        return first_limits[:10]
    
    async def get_hot_stocks(self) -> List[Dict]:
        return await self.get_popularity_stocks()
    
    async def get_market_summary(self, query_date: str = None) -> Dict:
        """获取市场整体状态总结（从数据库读取）

        Args:
            query_date: 查询日期，格式：YYYY-MM-DD，如果不提供则使用 get_display_trade_date()

        Returns:
            {
                "trade_date": "2025-01-31",
                "content": "总结内容...",
                "update_time": "2025-01-31T16:00:00",
                "modified_time": "2025-01-31T16:00:00"
            }
        """
        try:
            if query_date is None:
                query_date = get_display_trade_date()

            summary_data = db.get_market_status_summary(query_date)

            if summary_data:
                content = summary_data.get('summary_content', '')
                update_time = summary_data.get('update_time', '')
            else:
                content = ''
                update_time = ''

            result = {
                "trade_date": query_date,
                "content": content,
                "update_time": update_time,
                "modified_time": datetime.now().isoformat()
            }

            return result

        except Exception as e:
            logger.error(f"获取市场总结失败: {e}", exc_info=True)
            return {
                "trade_date": query_date or get_display_trade_date(),
                "content": "",
                "update_time": "",
                "modified_time": datetime.now().isoformat()
            }

    async def get_stock_popularity_data(self, trade_date: str = None):
        """
        获取标的热度板块数据（支持日期选择器）

        Args:
            trade_date: 交易日期（日期选择器选中的日期）

        Returns:
            标的热度数据
        """
        try:
            # 获取人气榜数据源 - 包含"人气、热度"相关数据
            # 关联关系：
            # - 调用 db_operations.get_popularity_sources() (line 2253)
            # - 返回 popularity_sources 给前端 /api/hot-stocks
            # - 前端 renderPopularitySubTabs() 使用此数据渲染子选项卡
            # 过滤说明：只获取"新高榜"数据源（半年新高、一年新高、历史新高）
            popularity_sources = db.get_popularity_sources()

            # 获取成交额榜类型
            amount_types = db.get_amount_types()

            # 获取人气榜标的数据
            # 关联关系：
            # - 调用 db_operations.get_popularity_stocks() (line 2369)
            # - 返回 popularity_data 给前端 /api/hot-stocks
            # - 前端 updateHotStocksCard() 使用此数据更新人气榜内容
            # 过滤说明：只处理"新高榜"数据源
            popularity_data = []
            for source in popularity_sources:
                stocks = db.get_popularity_stocks(source['source_id'], trade_date)
                popularity_data.append({
                    'source_id': source['source_id'],
                    'source_name': source['source_name'],
                    'stocks': stocks
                })

            amount_data = []
            for amt_type in amount_types:
                stocks = db.get_amount_stocks(amt_type['type_id'], trade_date)
                amount_data.append({
                    'type_id': amt_type['type_id'],
                    'type_name': amt_type['type_name'],
                    'stocks': stocks
                })

            return {
                "success": True,
                "trade_date": trade_date,
                "popularity_sources": popularity_sources,
                "popularity_data": popularity_data,
                "amount_types": amount_types,
                "amount_data": amount_data
            }
        except Exception as e:
            logger.error(f"获取标的热度板块数据失败: {e}", exc_info=True)
            return {"success": False, "message": str(e), "trade_date": trade_date, "popularity_sources": [], "popularity_data": [], "amount_types": [], "amount_data": []}


async def fetch_previous_em_data(query_date: str) -> List[Dict]:
    """通过 stock_zt_pool_previous_em(date=今天) 拉取【昨涨停今表现】数据

    返回结构与 fetch_spot_data 兼容: [{code, name, change_percent}, ...]
    覆盖范围: 昨天所有涨停的票（包含昨天首板 + 昨天连板），不覆盖非涨停的首板（极少数）。

    为何不用 stock_zh_a_spot_em()：东财 push2 全市场接口当前环境被 IP 限频，
    用户曾因此被封禁 1 个月。本接口走的限频更宽松，0.2s 单次返回 100+ 条。

    【重要：2026-07-24 经验沉淀】
    1. 东财封禁黑名单 (本机环境已不可用):
       - ak.stock_zh_a_spot_em() — 全市场行情 (用户被封 1 个月, **禁用**)
       - ak.stock_zh_a_hist()    — 单股 K 线 (同上, **禁用**)
       - ak.stock_bid_ask_em()   — 实时盘口 (同上, **禁用**)
    2. 东财仍可用接口 (限频宽松):
       - ak.stock_zt_pool_previous_em(date=今天) — **核心**, 0.2s/100+ 条
       - ak.stock_zt_pool_em(date=今天)         — 当日涨停池, 0.2s/40 条
       - ak.stock_zt_pool_strong_em(date=今天)  — 强势股池, 0.4s/56 条
    3. 任何东财接口调用前必须:
       - 加 2s 限速 (time.sleep 2)
       - 加 try/except fallback
       - 最多 2 次重试 (首调 + 1 次)
    4. 全局数据源优先级: limit_pool → previous_em → fetch_spot_data (最后)
    """
    try:
        import akshare as ak
        import math as _math
        date_compact = query_date.replace("-", "")

        def _call():
            return ak.stock_zt_pool_previous_em(date=date_compact)

        df = await asyncio.to_thread(_call)
        if df is None or len(df) == 0:
            logger.warning(f"stock_zt_pool_previous_em({date_compact}) 返回空")
            return []

        results = []
        for _, row in df.iterrows():
            try:
                code = str(row.get("代码", "")).zfill(6)
                pct_raw = row.get("涨跌幅")
                if pct_raw is None:
                    continue
                if isinstance(pct_raw, float) and _math.isnan(pct_raw):
                    continue
                results.append({
                    "code": code,
                    "name": str(row.get("名称", "")),
                    "change_percent": float(pct_raw),
                })
            except Exception as e:
                logger.debug(f"解析 previous_em 行失败: {e}")
                continue

        logger.info(f"fetch_previous_em_data({date_compact}) → {len(results)} 条")
        return results
    except Exception as e:
        logger.error(f"fetch_previous_em_data 失败: {e}")
        return []


@app.get("/api/limit-stats")
async def get_limit_statistics(query_date: str = None):
    data = await StockDataService().get_limit_stats(query_date)
    return data


@app.post("/api/limit-stats/save")
async def save_limit_statistics(
    trade_date: str = Body(..., description="交易日期，格式：YYYY-MM-DD"),
    first_limit: int = Body(..., description="首板数量"),
    continuous_limit: int = Body(..., description="连板数量"),
    exploded: int = Body(..., description="炸板数量"),
    limit_down: int = Body(..., description="跌停数量"),
    explode_rate: float = Body(..., description="炸板率（百分比）"),
    market_mood: int = Body(3, description="市场情绪（1=低迷, 2=谨慎, 3=正常, 4=活跃, 5=狂热）")
):
    """保存涨跌停统计数据（收盘后调用）"""
    try:
        affected = db.save_limit_stats(
            trade_date=trade_date,
            first_limit=first_limit,
            continuous_limit=continuous_limit,
            exploded=exploded,
            limit_down=limit_down,
            explode_rate=explode_rate,
            market_mood=market_mood
        )
        return {"success": True, "message": f"成功保存{affected}条记录"}
    except Exception as e:
        logger.error(f"保存涨跌停统计数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/limit-stats/analysis")
async def save_limit_analysis(
    trade_date: str = Body(..., description="交易日期，格式：YYYY-MM-DD"),
    analysis: str = Body(..., description="分析说明内容")
):
    """保存涨跌停分析说明"""
    try:
        affected = db.save_limit_analysis(trade_date, analysis)
        return {"success": True, "message": f"成功保存{affected}条记录"}
    except Exception as e:
        logger.error(f"保存涨跌停分析失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/limit-stats/refresh")
async def refresh_limit_statistics(
    targetDate: str = Body(..., embed=True, description="要刷新的目标日期"),
    forceRecreateTopics: bool = Body(False, description="强制重建题材卡片（忽略已有数据保护）")
):
    """手动刷新涨停统计数据（用户主动触发）

    业务逻辑：
    - 盘中：不获取数据，不创建题材卡片，提醒使用"📡 盘中刷新"按钮
    - 盘后：获取数据，自动创建题材卡片，联动刷新今日首板板块
    - 总是使用正式表（first_limits、topic_activations 等）
    - 清空临时表（临时数据视为错误数据）
    - 用于连板梯队等板块的盘后数据刷新

    Args:
        targetDate: 用户通过日期选择器选择的目标日期（格式：YYYY-MM-DD）
        forceRecreateTopics: 是否强制重建题材卡片（True=强制重建，False=保护已有数据）
    """
    try:
        from src.data_acquisition import DataAcquisitionService

        logger.info("手动刷新涨停数据开始")
        logger.info(f"用户选择的刷新日期: {targetDate}")

        # 判断是否在交易时段
        in_trading = is_in_trading_hours()
        logger.info(f"当前是否在交易时段: {in_trading}")

        if in_trading:
            # ===== 盘中情景 =====
            # 不获取数据，不创建题材卡片
            logger.info(f"[刷新] 盘中时段，不获取数据，请使用今日首板的'📡 盘中刷新'按钮")
            return {
                "success": True,
                "message": "当前为交易时段（9:25-15:00），请使用今日首板板块的'📡 盘中刷新'按钮获取盘中数据",
                "is_trading": True,
                "using_tmp_table": False,
                "auto_created_topics": False,
                "auto_create_reason": "盘中时段不自动创建",
                "activated_topics": [],
                "activated_topics_count": 0,
                "refresh_today_first_limits": False  # 不联动刷新今日首板
            }

        # ===== 盘外情景 =====
        service = DataAcquisitionService()

        # 清空临时表（临时数据视为错误数据）
        db.clear_first_limits_tmp_tables()

        # 确定查询日期
        if targetDate:
            query_date = targetDate
        else:
            query_date = get_query_trading_date()

        # 在刷新数据前检查正式表是否已有题材卡片
        has_activations_before_refresh = db.check_topic_activations_date_exists(query_date, table="topic_activations")

        # 获取数据（总是使用正式表）
        raw_result = service.fetch_and_save_limit_data(query_date, use_tmp_table=False)

        logger.info(f"手动刷新涨停数据完成: {raw_result}")

        # ===== 判断是否需要自动创建题材 =====
        # 规则：
        # 1. 检查 topic_activations 是否已有该日期的题材卡片
        # 2. 没有题材卡片：自动创建（初始化）
        # 3. 有题材卡片：不自动创建（保护用户手动操作，如删除的题材卡片）
        # 4. 强制重建参数会覆盖上述规则
        auto_create = False

        if forceRecreateTopics:
            # 强制重建：忽略已有数据保护
            auto_create = True
            logger.info(f"[刷新] 强制重建模式，执行自动创建题材")
        else:
            # 正常模式：使用刷新前的检查结果
            auto_create = not has_activations_before_refresh

            if not auto_create:
                logger.info(f"[刷新] 正式表已有 {query_date} 的题材卡片，跳过自动创建（保护用户手动操作）")
            else:
                logger.info(f"[刷新] 正式表没有 {query_date} 的题材卡片，执行自动创建（初始化）")

        # ===== 自动创建题材卡片 =====
        activated_topics = []

        if auto_create:
            logger.info(f"[刷新] 自动创建题材卡片")
            activated_topics = db.auto_create_topic_cards_for_date(
                query_date,
                topics_table="first_limit_topics",
                activations_table="topic_activations",
                first_limits_table="first_limits"
            )

            logger.info(f"[刷新] 激活题材: {len(activated_topics)} 个")
        else:
            logger.info(f"[刷新] 跳过自动创建题材卡片")

        # 检查是否需要提示新高数据限制
        high_data_updated = True  # 默认认为有更新
        if targetDate:
            try:
                from data.db_operations import get_latest_trading_date_from_db
                latest_trading_date = get_latest_trading_date_from_db()
                if latest_trading_date and targetDate != latest_trading_date:
                    high_data_updated = False
            except Exception as e:
                logger.warning(f"检查新高数据更新状态失败: {e}")

        result = {
            "success": raw_result.get("success", False) or (raw_result.get("first_limit_count", 0) > 0 or raw_result.get("continuous_limit_count", 0) > 0),
            "message": raw_result.get("message", ""),
            "first_limit_count": raw_result.get("first_limit_count", 0),
            "continuous_limit_count": raw_result.get("continuous_limit_count", 0),
            "exploded_count": raw_result.get("exploded_count", 0),
            "limit_down_count": raw_result.get("limit_down_count", 0),
            "total_records": raw_result.get("total_records", 0),
            "trade_date": query_date,
            "high_data_updated": high_data_updated,
            "high_data_message": "新高数据已更新" if high_data_updated else "新高数据未更新（仅支持刷新最新交易日）",
            "is_trading": False,
            "using_tmp_table": False,  # 总是使用正式表
            "auto_created_topics": auto_create,
            "auto_create_reason": ("强制重建" if forceRecreateTopics else 
                                  "初始化" if auto_create else 
                                  "保护用户手动操作"),
            "activated_topics": activated_topics,
            "activated_topics_count": len(activated_topics),
            "had_activations_before_refresh": has_activations_before_refresh,
            "refresh_today_first_limits": True  # 联动刷新今日首板板块
        }

        # ===== 盘后保存溢价快照 =====
        # 如果query_date是某个交易日，则保存该日首板标的在query_date的涨跌幅
        # 直接复用 fetch_and_save_limit_data 返回的当日涨停池 limit_pool，
        # 避免再次调用不稳定的 ak.stock_zh_a_spot_em()
        premium_limit_date = db.get_previous_trading_day(query_date)
        premium_saved_count = 0
        if premium_limit_date:
            logger.info(f"[盘后刷新] 保存溢价快照：首板日期 {premium_limit_date}，快照日期 {query_date}")
            try:
                # 获取上一交易日的首板标的
                premium_limit_stocks = db.get_first_limits_by_date(premium_limit_date, table="first_limits")
                if premium_limit_stocks:
                    # 数据源优先级: limit_pool (今日涨停池) → previous_em (昨涨停今表现) → fetch_spot_data (全市场, 易被封)
                    spot_data = raw_result.get("limit_pool", []) if raw_result else []
                    if not spot_data:
                        # 降级：用 stock_zt_pool_previous_em 拉"昨涨停今表现"
                        logger.warning("[盘后刷新] 涨停池为空，降级尝试 stock_zt_pool_previous_em")
                        spot_data = await fetch_previous_em_data(query_date)
                        if not spot_data:
                            # 终极降级：尝试 fetch_spot_data（非交易时段可能失败, 易被东财限频）
                            logger.warning("[盘后刷新] previous_em 仍空，最终降级 fetch_spot_data")
                            spot_data = await service.fetch_spot_data()
                    # 构建溢价数据
                    premiums_data = []
                    for stock in premium_limit_stocks:
                        stock_id = stock.get("stock_id")
                        stock_code = stock.get("code", "")
                        change_percent = None
                        for spot in spot_data:
                            if spot.get("code") == stock_code:
                                change_percent = spot.get("change_percent")
                                break
                        if stock_id and change_percent is not None:
                            premiums_data.append({
                                "stock_id": stock_id,
                                "limit_date": premium_limit_date,
                                "premium_date": query_date,
                                "change_percent": change_percent
                            })
                    # 批量保存
                    premium_saved_count = db.save_first_limit_premiums_batch(premiums_data)
                    logger.info(f"[盘后刷新] 溢价快照保存完成：{premium_saved_count} 条")
            except Exception as e:
                logger.error(f"[盘后刷新] 保存溢价快照失败: {e}", exc_info=True)

        result["premium_saved_count"] = premium_saved_count
        result["premium_limit_date"] = premium_limit_date

        return result
    except Exception as e:
        logger.error(f"手动刷新涨停数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/limit-stats/intraday-refresh")
async def refresh_intraday_data(targetDate: str = Body(..., embed=True, description="要刷新的目标日期")):
    """盘中刷新今日首板数据（使用临时表）

    业务逻辑：
    - 仅在交易时段可用
    - 使用临时表（first_limits_tmp、topic_activations_tmp 等）
    - 刷新当前交易日的数据
    - 总是自动创建题材卡片（因为盘中数据会变化）

    Args:
        targetDate: 用户通过日期选择器选择的目标日期（格式：YYYY-MM-DD），用于今日首板板块
    """
    try:
        # 检查是否在交易时段
        if not is_in_trading_hours():
            logger.warning("盘中刷新功能仅在交易时段（9:25-15:00）可用")
            raise HTTPException(status_code=400, detail="盘中刷新功能仅在交易时段（9:25-15:00）可用")

        from src.data_acquisition import DataAcquisitionService

        logger.info("盘中刷新今日首板数据开始")
        logger.info(f"用户选择的刷新日期: {targetDate}")

        service = DataAcquisitionService()

        # 清空临时表
        db.clear_first_limits_tmp_tables()

        # 确定查询日期（总是使用 get_query_trading_date()，不论用户选择什么日期）
        # 因为盘中刷新的是当日实时数据，而不是历史数据
        query_date = get_query_trading_date()

        # 获取数据（使用临时表）
        raw_result = service.fetch_and_save_limit_data(query_date, use_tmp_table=True)

        logger.info(f"盘中刷新完成: {raw_result}")

        # ===== 总是自动创建题材卡片（盘中数据会变化） =====
        activated_topics = db.auto_create_topic_cards_for_date(
            query_date,
            topics_table="first_limit_topics_tmp",
            activations_table="topic_activations_tmp",
            first_limits_table="first_limits_tmp"
        )

        logger.info(f"盘中刷新 激活题材: {len(activated_topics)} 个")

        # ===== 获取溢价数据（盘中实时获取） =====
        premiums = []
        limit_date = db.get_previous_trading_day(query_date)
        if limit_date:
            logger.info(f"获取溢价数据：首板日期 {limit_date}，快照日期 {query_date}")
            # 获取上一交易日的首板标的
            limit_stocks = db.get_first_limits_by_date(limit_date, table="first_limits")
            if limit_stocks:
                # 数据源优先级: previous_em (昨涨停今表现) → fetch_spot_data (全市场, 易被封)
                spot_data = await fetch_previous_em_data(query_date)
                if not spot_data:
                    # 降级：尝试 fetch_spot_data（非交易时段可能失败, 易被东财限频）
                    logger.warning("[盘中刷新] previous_em 为空，降级 fetch_spot_data")
                    spot_data = await service.fetch_spot_data()
                # 匹配涨跌幅
                for stock in limit_stocks:
                    stock_code = stock.get("code", "")
                    change_percent = None
                    for spot in spot_data:
                        if spot.get("code") == stock_code:
                            change_percent = spot.get("change_percent")
                            break
                    premiums.append({
                        "stock_id": stock.get("stock_id"),
                        "code": stock_code,
                        "name": stock.get("name"),
                        "change_percent": change_percent
                    })
                # 落库正式表（盘中数据, 盘后刷新会覆盖）
                try:
                    premiums_for_db = [
                        {
                            "stock_id": p["stock_id"],
                            "limit_date": limit_date,
                            "premium_date": query_date,
                            "change_percent": p["change_percent"],
                        }
                        for p in premiums
                    ]
                    saved = db.save_first_limit_premiums_batch(premiums_for_db)
                    logger.info(f"盘中溢价快照已落库: {saved}/{len(premiums_for_db)}")
                except Exception as e:
                    logger.warning(f"盘中溢价快照落库失败: {e}")
            logger.info(f"获取到 {len(premiums)} 条溢价数据")

        result = {
            "success": raw_result.get("success", False) or (raw_result.get("first_limit_count", 0) > 0),
            "message": raw_result.get("message", ""),
            "first_limit_count": raw_result.get("first_limit_count", 0),
            "continuous_limit_count": raw_result.get("continuous_limit_count", 0),
            "exploded_count": raw_result.get("exploded_count", 0),
            "limit_down_count": raw_result.get("limit_down_count", 0),
            "total_records": raw_result.get("total_records", 0),
            "trade_date": query_date,
            "is_trading": True,
            "using_tmp_table": True,  # 使用临时表
            "auto_created_topics": True,
            "activated_topics": activated_topics,
            "activated_topics_count": len(activated_topics),
            "premiums": premiums,
            "premium_limit_date": limit_date
        }

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"盘中刷新今日首板数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/first-limit-premiums/{date}")
async def get_first_limit_premiums(date: str):
    """获取首板溢价数据

    业务逻辑：
    - 计算指定日期的上一个交易日
    - 如果date是今天且在盘中 → 实时获取涨跌幅
    - 其他情况 → 从数据库读取溢价快照
    - 返回该交易日所有首板标的在date的涨跌幅

    Args:
        date: 当前显示的日期（格式：YYYY-MM-DD），即溢价快照日期
    """
    try:
        logger.info(f"获取首板溢价数据，快照日期：{date}")

        # 获取上一个交易日（首板日期）
        limit_date = db.get_previous_trading_day(date)
        if not limit_date:
            logger.warning(f"无法获取 {date} 的上一个交易日")
            return {"premiums": [], "limit_date": None, "snapshot_date": date}

        logger.info(f"首板日期：{limit_date}，快照日期：{date}")

        # 判断是否是今天且在盘中 → 实时获取
        today_str = get_query_trading_date()
        is_today = (date == today_str)
        in_trading = is_in_trading_hours()

        if is_today and in_trading:
            # 盘中实时获取
            logger.info("盘中实时获取溢价数据")
            from src.data_acquisition import DataAcquisitionService
            service = DataAcquisitionService()

            # 获取上一交易日的首板标的
            limit_stocks = db.get_first_limits_by_date(limit_date, table="first_limits")
            if not limit_stocks:
                logger.info(f"上一交易日 {limit_date} 无首板标的")
                return {"premiums": [], "limit_date": limit_date, "snapshot_date": date}

            # 数据源优先级: previous_em (昨涨停今表现) → fetch_spot_data (全市场, 易被封)
            spot_data = await fetch_previous_em_data(date)
            if not spot_data:
                # 降级：尝试 fetch_spot_data（非交易时段可能失败, 易被东财限频）
                logger.warning("[GET 溢价] previous_em 为空，降级 fetch_spot_data")
                spot_data = await service.fetch_spot_data()

            # 匹配涨跌幅
            premiums = []
            for stock in limit_stocks:
                stock_code = stock.get("code", "")
                # 从实时行情中查找
                change_percent = None
                for spot in spot_data:
                    if spot.get("code") == stock_code:
                        change_percent = spot.get("change_percent")
                        break
                premiums.append({
                    "stock_id": stock.get("stock_id"),
                    "code": stock_code,
                    "name": stock.get("name"),
                    "change_percent": change_percent
                })

            return {
                "premiums": premiums,
                "limit_date": limit_date,
                "snapshot_date": date,
                "is_realtime": True
            }
        else:
            # 从数据库读取快照
            logger.info("从数据库读取溢价快照")
            premiums = db.get_first_limit_premiums(limit_date, date)
            return {
                "premiums": premiums,
                "limit_date": limit_date,
                "snapshot_date": date,
                "is_realtime": False
            }

    except Exception as e:
        logger.error(f"获取首板溢价数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/first-limit-premiums/snapshot")
async def save_first_limit_premiums_snapshot(
    date: str = Body(..., description="快照日期，即溢价观察日"),
    force: bool = Body(False, description="强制覆盖已有快照")
):
    """保存首板溢价快照

    业务逻辑：
    - 获取指定日期的上一个交易日的首板标的
    - 通过 ak.stock_zt_pool_previous_em(date) 拉取【昨涨停今表现】接口，匹配这些标的的涨跌幅
    - 存入 first_limit_premiums 表

    注意：ak.stock_zh_a_spot_em() 全市场行情接口在当前环境已被东财封禁，
    改用 stock_zt_pool_previous_em(date=今天) 拿"昨涨停今表现"专用接口。
    该接口单次调用 0.2s，1 次即可覆盖 100% 昨日涨停标的（含昨日首板）。

    Args:
        date: 快照日期（格式：YYYY-MM-DD），即溢价观察日
        force: 是否强制覆盖已有快照
    """
    import time
    try:
        logger.info(f"保存首板溢价快照，快照日期：{date}（force={force}）")

        # 获取上一个交易日（首板日期）
        limit_date = db.get_previous_trading_day(date)
        if not limit_date:
            logger.warning(f"无法获取 {date} 的上一个交易日，跳过快照保存")
            return {"success": True, "message": "无上一交易日，跳过快照保存", "saved_count": 0}

        logger.info(f"首板日期：{limit_date}，快照日期：{date}")

        # 幂等保护：已有快照且不强制，跳过
        if not force and db.has_first_limit_premiums(limit_date, date):
            existing = db.get_first_limit_premiums(limit_date, date)
            logger.info(f"快照已存在（{len(existing)} 条），跳过保存")
            return {
                "success": True,
                "message": f"快照已存在（{len(existing)} 条），传 force=true 强制覆盖",
                "saved_count": 0,
                "skipped": True,
                "limit_date": limit_date,
                "premium_date": date
            }

        # 获取上一交易日的首板标的
        limit_stocks = db.get_first_limits_by_date(limit_date, table="first_limits")
        if not limit_stocks:
            logger.info(f"上一交易日 {limit_date} 无首板标的，跳过快照保存")
            return {"success": True, "message": "无首板标的，跳过快照保存", "saved_count": 0}

        # === 拉取昨涨停今表现（专用接口，避免被东财限频） ===
        # 接口语义：date=今天，返回昨天涨停的票在今天的涨跌幅
        # 覆盖关系：previous_em ⊃ 昨天所有涨停票 ⊃ 昨天首板
        date_compact = date.replace("-", "")
        prev_em_df = None
        last_err = None
        for attempt in range(1, 3):  # 最多 2 次（首调 + 1 次重试）
            try:
                logger.info(f"ak.stock_zt_pool_previous_em({date_compact}) 第 {attempt} 次尝试")
                prev_em_df = await asyncio.to_thread(
                    ak.stock_zt_pool_previous_em, date=date_compact
                )
                break  # 成功
            except Exception as e:
                last_err = e
                logger.warning(f"ak.stock_zt_pool_previous_em 第 {attempt} 次失败: {e}")
                if attempt < 2:
                    time.sleep(2)  # 限速 2s

        if prev_em_df is None or len(prev_em_df) == 0:
            logger.error(f"ak.stock_zt_pool_previous_em 全部重试失败: {last_err}")
            raise HTTPException(
                status_code=503,
                detail=f"东财昨涨停今表现接口不可用，已重试仍失败: {last_err}"
            )

        # 接口列名是中文：代码、名称、涨跌幅
        # 构造 code -> change_percent 字典
        import math as _math
        change_pct_map = {}
        for _, row in prev_em_df.iterrows():
            try:
                code = str(row.get("代码", "")).zfill(6)  # 补齐 6 位
                pct_raw = row.get("涨跌幅")
                if pct_raw is None:
                    continue
                # 兼容 NaN (float('nan'))
                if isinstance(pct_raw, float) and _math.isnan(pct_raw):
                    continue
                change_pct_map[code] = float(pct_raw)
            except Exception as e:
                logger.debug(f"解析 previous_em 行失败: {e}, row={dict(row)}")
                continue

        logger.info(
            f"ak.stock_zt_pool_previous_em 返回 {len(prev_em_df)} 条, "
            f"有效 {len(change_pct_map)} 条"
        )

        # === 匹配涨跌幅并批量保存 ===
        premiums_data = []
        missing_codes = []
        for stock in limit_stocks:
            stock_id = stock.get("stock_id")
            stock_code = str(stock.get("code", "")).zfill(6)
            if not stock_id:
                continue

            change_percent = change_pct_map.get(stock_code)
            if change_percent is None:
                missing_codes.append(stock_code)
                continue

            premiums_data.append({
                "stock_id": stock_id,
                "limit_date": limit_date,
                "premium_date": date,
                "change_percent": change_percent,
            })

        saved_count = db.save_first_limit_premiums_batch(premiums_data) if premiums_data else 0

        logger.info(
            f"溢价快照保存完成：{saved_count}/{len(limit_stocks)} 条 "
            f"（previous_em 缺失 {len(missing_codes)} 条: {missing_codes[:5]}{'...' if len(missing_codes) > 5 else ''}）"
        )

        return {
            "success": True,
            "message": f"保存 {saved_count}/{len(limit_stocks)} 条溢价快照"
                       + (f"，{len(missing_codes)} 条未匹配（停牌/退市等）" if missing_codes else ""),
            "saved_count": saved_count,
            "expected_count": len(limit_stocks),
            "missing_count": len(missing_codes),
            "missing_codes": missing_codes[:20],  # 最多返前 20 个
            "limit_date": limit_date,
            "premium_date": date
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存首板溢价快照失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/market-summary/save")
async def save_market_summary(
    trade_date: str = Body(..., description="交易日期，格式：YYYY-MM-DD"),
    summary_content: str = Body(..., description="总结内容")
):
    """保存市场整体状态总结"""
    try:
        affected = db.save_market_status_summary(trade_date, summary_content)
        return {"success": True, "message": f"成功保存{affected}条记录"}
    except Exception as e:
        logger.error(f"保存市场整体状态总结失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/market-summary/{trade_date}")
async def get_market_summary_by_date(trade_date: str):
    """获取指定交易日的市场整体状态总结"""
    try:
        data = await StockDataService().get_market_summary(trade_date)
        return data
    except Exception as e:
        logger.error(f"获取市场整体状态总结失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/continuous-limits/analysis/save")
async def save_continuous_limits_analysis(
    trade_date: str = Body(..., description="交易日期，格式：YYYY-MM-DD"),
    analysis: str = Body(..., description="分析说明内容")
):
    """保存连板梯队分析说明"""
    try:
        affected = db.save_continuous_limits_analysis(trade_date, analysis)
        return {"success": True, "message": f"成功保存{affected}条记录"}
    except Exception as e:
        logger.error(f"保存连板梯队分析失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/continuous-limits/analysis/{trade_date}")
async def get_continuous_limits_analysis_by_date(trade_date: str):
    """获取指定交易日的连板梯队分析"""
    try:
        data = await StockDataService().get_continuous_limit_analysis(trade_date)
        return data
    except Exception as e:
        logger.error(f"获取连板梯队分析失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/continuous-limits/{trade_date}")
async def get_continuous_limits_by_date(trade_date: str):
    """获取指定交易日的连板历史数据"""
    try:
        data = db.get_continuous_limits_by_date(trade_date)
        return {"continuous_limits": data}
    except Exception as e:
        logger.error(f"获取连板历史数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/continuous-limits")
async def get_continuous_limits():
    data = await StockDataService().get_continuous_limits()
    return {"continuous_limits": data}

@app.get("/api/continuous-limit-analysis")
async def get_continuous_limit_analysis():
    data = await StockDataService().get_continuous_limit_analysis()
    return data

@app.get("/api/sectors")
async def get_sectors():
    data = await StockDataService().get_sector_analysis()
    return data

@app.get("/api/change-stocks")
async def get_change_stocks():
    data = await StockDataService().get_change_stocks()
    return data

@app.get("/api/first-limits")
async def get_first_limits(date: str = None):
    """获取首板数据，指定日期则从数据库读取，否则使用 AkShare 实时获取"""
    try:
        if date:
            # 从数据库读取指定日期的首板数据
            records = db.get_first_limits_by_date(date)

            # 转换为前端格式
            first_limits = []
            for record in records:
                first_limits.append({
                    'code': record['code'],
                    'name': record['name'],
                    'price': record['price'],
                    'change_percent': 10.0 if record['limit_type'] == '10%' else (20.0 if record['limit_type'] == '20%' else 30.0),
                    'first_time': record['first_time'] or '09:30',
                    'sector': record['sector'] or '综合',
                    'reason': record['reason'],
                    'stock_id': record['stock_id'],
                    'id': record['id']
                })

            # 按时间排序
            first_limits.sort(key=lambda x: x['first_time'])
            return {"first_limits": first_limits}
        else:
            # 使用 AkShare 实时获取（临时方案，后续会逐步替换）
            data = await StockDataService().get_first_limits()
            return {"first_limits": data}
    except Exception as e:
        logger.error(f"获取首板数据失败: {e}", exc_info=True)
        return {"first_limits": []}

@app.get("/api/market-summary")
async def get_market_summary():
    data = await StockDataService().get_market_summary()
    return data

@app.post("/api/save-rotation-analysis")
async def save_rotation_analysis(analysis: Dict = Body(...)):
    """保存题材轮动分析

    支持两种日期格式：
    - day: 相对天数（如 -1 表示昨天）
    - date: 绝对日期（YYYY-MM-DD 格式）

    优先级：day > date > 今天
    """
    try:
        topic = analysis.get('topic')
        content = analysis.get('content', '')
        stage = analysis.get('stage')
        timestamp = analysis.get('timestamp', datetime.now().isoformat())

        # 获取日期：优先使用 day，然后 date，最后默认今天
        day = analysis.get('day')
        date = analysis.get('date', datetime.now().strftime("%Y-%m-%d"))

        if day is not None:
            # 如果提供了 day，计算绝对日期
            target_date = datetime.now().date() + timedelta(days=day)
            date = target_date.strftime("%Y-%m-%d")
        elif 'date' in analysis and analysis['date']:
            # 如果提供了 date，直接使用
            date = analysis['date']
        else:
            # 如果都没提供，使用今天
            date = datetime.now().strftime("%Y-%m-%d")

        success = db.save_analysis(topic, content, date, timestamp=timestamp, stage=stage)

        if success:
            return {"success": True, "message": "分析保存成功"}
        else:
            return {"success": False, "message": "保存失败"}
    except Exception as e:
        logger.error(f"保存题材轮动分析失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.get("/api/rotation-history")
async def get_rotation_history(offset: int = 0):
    """加载题材轮动历史数据，从数据库读取最近5个交易日"""
    try:
        display_trade_date = get_display_trade_date(query_existing_data=False)
        display_date_obj = datetime.strptime(display_trade_date, "%Y-%m-%d").date()

        # 使用今天（而非展示日期）作为day值的基准
        today = datetime.now().date()

        recent_trading_dates = get_recent_trading_days(5, display_trade_date)
        
        if not recent_trading_dates:
            logger.warning("无法获取交易日，返回空数据")
            return {"topics": [], "dates": []}

        conn = db._get_connection()
        cursor = conn.cursor()

        # 获取所有活跃题材
        cursor.execute('SELECT topic_id, topic_name FROM topics WHERE is_active = 1')
        all_topics = cursor.fetchall()

        # 初始化所有题材
        topics_data = {}
        for topic_id, topic_name in all_topics:
            topics_data[topic_name] = {
                'name': topic_name,
                'days': {},
                'stages': {},
                'hot': False
            }

        # 查询这些交易日的题材分析数据
        placeholders = ','.join(['?'] * len(recent_trading_dates))
        cursor.execute(f'''
            SELECT t.topic_name, ra.date, ra.content, ra.is_active, ra.stage
            FROM rotation_actives ra
            JOIN topics t ON ra.topic_id = t.topic_id
            WHERE ra.date IN ({placeholders})
            ORDER BY t.topic_name, ra.date
        ''', tuple(recent_trading_dates))

        records = cursor.fetchall()
        conn.close()

        # 填充数据到 topics_data
        for topic_name, date_str, content, is_active, stage in records:
            if topic_name not in topics_data:
                continue

            # 计算相对天数（相对于今天）
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            day_diff = int((date_obj - today).days)

            topics_data[topic_name]['days'][str(day_diff)] = content or ''
            topics_data[topic_name]['stages'][str(day_diff)] = stage

            # 如果某天有活跃内容，标记为热门
            if is_active == 1 and content and content.strip():
                topics_data[topic_name]['hot'] = True

        # 确保包含所有交易日（即使没有数据）
        for topic_data in topics_data.values():
            for date_str in recent_trading_dates:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                day_diff = int((date_obj - today).days)

                day_str = str(day_diff)
                if day_str not in topic_data['days']:
                    topic_data['days'][day_str] = ''

        # 转换为列表并按日期排序
        topics_result = []
        day_keys = []
        for topic_data in topics_data.values():
            # 获取day值的列表并排序（从早到晚）
            topic_day_keys = sorted([int(d) for d in topic_data['days'].keys()])
            if not day_keys:
                day_keys = topic_day_keys

            # 创建有序的days字典
            ordered_days = {}
            for day in topic_day_keys:
                ordered_days[str(day)] = topic_data['days'][str(day)]

            topics_result.append({
                'name': topic_data['name'],
                'days': ordered_days,
                'hot': topic_data['hot']
            })

        # 按最近一天是否有内容排序
        if day_keys:
            latest_day = str(day_keys[-1])
            topics_result.sort(key=lambda x: len(x['days'][latest_day]) if latest_day in x['days'] else 0, reverse=True)

        # 生成日期标签
        dates_result = []
        for day in day_keys:
            date = today + timedelta(days=day)
            date_str = date.strftime("%Y-%m-%d")
            if day == 0:
                label = "今日"
            elif day == -1:
                label = "昨日"
            else:
                label = date.strftime("%m-%d")
            dates_result.append({'day': str(day), 'label': label, 'date': date_str})

        return {
            "topics": topics_result,
            "dates": dates_result
        }
    except Exception as e:
        logger.error(f"加载轮动历史失败: {e}")
        return {"error": str(e)}
    
@app.post("/api/remove-topic-from-date")
async def remove_topic_from_date(topic_data: Dict = Body(...)):
    """根据日期删除题材记录"""
    try:
        topic = topic_data.get('topic')
        date = topic_data.get('date')

        if not topic:
            return {"success": False, "message": "题材名称不能为空"}

        if not date:
            return {"success": False, "message": "必须提供 date 参数"}

        success = db.remove_analysis(topic, date)

        if success:
            logger.info(f"删除成功 - 主题: {topic}, 日期: {date}")
            return {"success": True, "message": "删除成功"}
        else:
            logger.info(f"删除失败 - 主题: {topic}, 日期: {date}")
            return {"success": False, "message": f"未找到相关记录 (topic={topic}, date={date})"}
    except Exception as e:
        logger.error(f"删除题材失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.post("/api/activate-topic")
async def activate_topic(topic_data: Dict = Body(...)):
    """激活/创建题材（今日首板板块快速添加功能）"""
    try:
        topic_name = topic_data.get('topic_name')
        query_date = topic_data.get('queryDate')

        if not topic_name:
            return {"success": False, "message": "题材名称不能为空"}

        # 获取当前查看的交易日期（开盘后应查看的交易日）
        if query_date:
            target_date = query_date
        else:
            target_date = db.get_query_trading_date()

        if not target_date:
            return {"success": False, "message": "无法获取交易日期"}

        # 调用数据库方法激活题材
        success, topic_id, message = db.activate_topic(topic_name, target_date)

        if success:
            return {
                "success": True,
                "message": "激活成功",
                "topic_id": topic_id
            }
        else:
            return {
                "success": False,
                "message": message
            }
    except Exception as e:
        logger.error(f"激活题材失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.get("/api/check-topic-stock-relations")
async def check_topic_stock_relations(topic_id: int):
    """检查题材在topic_stock_relations表中是否有持久关联
    
    今日首板删除题材的逻辑（重要！）：
    - 只检查topic_stock_relations表中的持久关联
    - 不检查first_limit_topics表中的临时关联
    - 如果topic_stock_relations中有该题材的关联，则不能删除整个题材
    - 避免删除题材后，其他日期的first_limit_topics中的topic_id变成孤儿引用
    
    参数说明：
    - topic_id: 题材ID
    
    返回值：
    - has_relations: 是否有持久关联
    - stock_count: 关联的股票数量
    """
    try:
        relations = db.check_topic_stock_relations(topic_id)
        stock_count = len(relations) if relations else 0
        
        return {
            "has_relations": stock_count > 0,
            "stock_count": stock_count
        }
    except Exception as e:
        logger.error(f"检查题材持久关联失败: {e}", exc_info=True)
        return {"error": str(e), "has_relations": False, "stock_count": 0}

@app.post("/api/delete-topic-card")
async def delete_topic_card(topic_data: Dict = Body(...)):
    """删除题材卡片（移除今日激活）"""
    try:
        topic_id = topic_data.get('topic_id')
        query_date = topic_data.get('queryDate')

        if not topic_id:
            return {"success": False, "message": "题材ID不能为空"}

        # 获取当前查看的交易日期（开盘后应查看的交易日）
        if query_date:
            target_date = query_date
        else:
            target_date = db.get_query_trading_date()

        if not target_date:
            return {"success": False, "message": "无法获取交易日期"}

        # 调用数据库方法删除题材激活
        success = db.remove_topic_activation(topic_id, target_date)

        if success:
            logger.info(f"删除题材卡片成功: topic_id={topic_id}, date={target_date}")
            return {"success": True, "message": "删除成功"}
        else:
            logger.warning(f"删除题材卡片失败: topic_id={topic_id}, date={target_date}")
            return {"success": False, "message": "删除失败：无可用的记录"}
    except Exception as e:
        logger.error(f"删除题材卡片失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.post("/api/delete-topic")
async def delete_topic(topic_data: Dict = Body(...)):
    """删除整个题材（包括所有关联数据）"""
    try:
        topic_id = topic_data.get('topic_id')

        if not topic_id:
            return {"success": False, "message": "题材ID不能为空"}

        success = db.delete_topic(topic_id)

        if success:
            logger.info(f"删除题材成功: topic_id={topic_id}")
            return {"success": True, "message": "删除成功"}
        else:
            logger.warning(f"删除题材失败: topic_id={topic_id}")
            return {"success": False, "message": "删除失败"}
    except Exception as e:
        logger.error(f"删除题材失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.post("/api/update-topic-stage")
async def update_topic_stage(data: Dict = Body(...)):
    """更新题材的阶段状态"""
    try:
        topic = data.get('topic')
        stage = data.get('stage')
        date = data.get('date')

        if not topic or not date:
            return {"success": False, "message": "参数不完整"}

        success = db.update_topic_stage(topic, stage, date)

        if success:
            return {"success": True, "message": "状态更新成功"}
        else:
            return {"success": False, "message": "状态更新失败"}
    except Exception as e:
        logger.error(f"更新题材状态失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.get("/api/topics-by-stage")
async def get_topics_by_stage(stage: str = "explosion", days: int = 24):
    """
    获取最近N个交易日中标记过特定状态的题材

    参数：
    - stage: 状态筛选（startup/explosion/maintain/divergence/recede/backflow/all）
    - days: 查询的天数（默认24个交易日）

    返回：
    - topics: 题材列表（去重后的题材名称）
    - dates: 查询的日期范围
    - count: 题材数量
    """
    try:
        conn = db._get_connection()
        cursor = conn.cursor()

        # 获取最近交易日期
        cursor.execute('SELECT MAX(date) FROM rotation_actives')
        result = cursor.fetchone()
        if not result or not result[0]:
            conn.close()
            return {"topics": [], "dates": [], "count": 0}

        latest_date = result[0]

        # 获取最近N个交易日
        cursor.execute('''
            SELECT date FROM trading_days
            WHERE date <= ? AND is_active = 1
            ORDER BY date DESC
            LIMIT ?
        ''', (latest_date, days))

        date_rows = cursor.fetchall()
        trading_date_list = [row[0] for row in date_rows]

        if not trading_date_list:
            conn.close()
            return {"topics": [], "dates": [], "count": 0}

        # 查询这些日期中标记为指定状态的题材
        placeholders = ','.join(['?'] * len(trading_date_list))
        query = f'''
            SELECT DISTINCT t.topic_name
            FROM rotation_actives ra
            JOIN topics t ON ra.topic_id = t.topic_id
            WHERE ra.date IN ({placeholders})
        '''

        if stage != 'all':
            query += ' AND ra.stage = ?'

        cursor.execute(query, tuple(trading_date_list) + (stage,) if stage != 'all' else tuple(trading_date_list))

        topic_rows = cursor.fetchall()
        conn.close()

        topics = [row[0] for row in topic_rows]

        return {
            "topics": topics,
            "dates": trading_date_list,
            "count": len(topics)
        }
    except Exception as e:
        logger.error(f"获取题材状态列表失败: {e}", exc_info=True)
        return {"error": str(e), "topics": [], "dates": [], "count": 0}

@app.get("/api/all-topics")
async def get_all_topics():
    """获取所有题材列表，按最近添加排序"""
    try:
        topics_list = db.get_all_topics()

        return {"topics": topics_list}
    except Exception as e:
        logger.error(f"获取题材列表失败: {e}")
        return {"error": str(e)}

@app.get("/api/topic-details")
async def get_topic_details(topic_name: str = "", days: int = 24):
    """
    获取题材的详细历史数据（用于图表展示）

    参数：
    - topic_name: 题材名称
    - days: 查询的天数（默认24个交易日）

    返回：
    - success: 是否成功
    - topic_name: 题材名称
    - dates: 日期列表（按最近的日期在前）
    - first_limits: 每日首板数量
    - continuous_limits: 每日连扳数量
    - stages: 每日状态
    """
    try:
        logger.info(f"[DEBUG] /api/topic-details 被调用: topic_name={topic_name}, days={days}")

        if not topic_name:
            return {"success": False, "error": "题材名称不能为空"}

        # 获取题材ID
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT topic_id FROM topics WHERE topic_name = ?', (topic_name,))
        result = cursor.fetchone()
        conn.close()

        if not result:
            return {"success": False, "error": "题材不存在", "topic_name": topic_name}

        topic_id = result[0]

        # 获取统计数据
        stats = db.get_topic_statistics_by_days(topic_id, days)

        # 打印0209这一天的数据
        if stats['dates']:
            for i, date in enumerate(stats['dates']):
                if '2025-02-09' in date or '0209' in date:
                    logger.info(f"[DEBUG 0209] /api/topic-details: topic={topic_name}, date={date}, first_limit={stats['first_limits'][i]}, continuous_limit={stats['continuous_limits'][i]}, stage={stats['stages'][i]}")

        return {
            "success": True,
            "topic_name": topic_name,
            "dates": stats["dates"],
            "first_limits": stats["first_limits"],
            "continuous_limits": stats["continuous_limits"],
            "stages": stats["stages"]
        }
    except Exception as e:
        logger.error(f"获取题材详情失败: {e}", exc_info=True)
        return {"success": False, "error": str(e), "topic_name": topic_name}

@app.get("/api/topic-active-stocks")
async def get_topic_active_stocks(topic_name: str = "", days: int = 24):
    """
    获取题材近期活跃标的（近N个交易日有过涨停的股票）

    参数：
    - topic_name: 题材名称
    - days: 查询的天数（默认24个交易日）

    返回：
    - success: 是否成功
    - topic_name: 题材名称
    - stocks: 活跃标的列表
    """
    try:
        if not topic_name:
            return {"success": False, "error": "题材名称不能为空"}

        stocks = db.get_topic_active_stocks(topic_name, days)

        return {
            "success": True,
            "topic_name": topic_name,
            "stocks": stocks
        }
    except Exception as e:
        logger.error(f"获取题材活跃标的失败: {e}", exc_info=True)
        return {"success": False, "error": str(e), "topic_name": topic_name}

@app.get("/api/topic-trend-stocks")
async def get_topic_trend_stocks(topic_name: str = "", days: int = 24):
    """
    获取题材的趋势标历史（近N个交易日中的趋势标的入选数据）

    参数：
    - topic_name: 题材名称
    - days: 查询的天数（默认24个交易日）

    返回：
    - success: 是否成功
    - data: 趋势标数据（按入选次数和日期分组）
    """
    try:
        if not topic_name:
            return {"success": False, "error": "题材名称不能为空"}

        data = db.get_topic_trend_stocks(topic_name, days)

        return {
            "success": True,
            "topic_name": topic_name,
            "data": data
        }
    except Exception as e:
        logger.error(f"获取题材趋势标失败: {e}", exc_info=True)
        return {"success": False, "error": str(e), "topic_name": topic_name}

@app.post("/api/remove-topic-from-date")
async def remove_topic_from_date(topic_data: Dict = Body(...)):
    """删除某天某题材的记录"""
    try:
        topic = topic_data.get('topic')
        date = topic_data.get('date')

        if not topic:
            return {"success": False, "message": "题材名称不能为空"}

        if not date:
            return {"success": False, "message": "必须提供 date 参数"}

        success = db.remove_analysis(topic, date)

        if success:
            logger.info(f"删除成功 - 主题: {topic}, 日期: {date}")
            return {"success": True, "message": "删除成功"}
        else:
            logger.info(f"删除失败 - 主题: {topic}, 日期: {date}")
            return {"success": False, "message": f"未找到相关记录 (topic={topic}, date={date})"}
    except Exception as e:
        logger.error(f"删除题材失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

def try_parse_int(value):
    """尝试将值转换为整数，失败则返回None"""
    try:
        if value is None:
            return None
        return int(value)
    except (ValueError, TypeError):
        return None

def get_market_summary_query_date(queryDate: str = None) -> str:
    """获取市场整体状态总结和连板梯队的查询日期

    规则：
    - 15:00之前：返回数据库中的最新交易日
    - 15:00及之后：
        - 如果今天是交易日（从 trading_days 表判断），返回今天
        - 否则返回数据库中的最新交易日
    - 如果用户传递了 queryDate，直接返回 queryDate

    Args:
        queryDate: 用户选择的查询日期

    Returns:
        应该查询的交易日期字符串（格式：YYYY-MM-DD）
    """
    if queryDate:
        return queryDate

    now = datetime.now()
    current_date = now.date().strftime("%Y-%m-%d")
    current_time = now.time()

    market_close_time = time(15, 0)

    # 从 trading_days 表获取最新交易日（而不是从 limit_stats 表）
    latest_trading_date_from_db = get_latest_trading_date_from_db()

    if latest_trading_date_from_db is None:
        # 数据库没有数据，返回最近工作日
        logger.info("数据库中没有交易日数据，返回最近工作日")
        latest_trading_date_from_db = get_last_trading_day()

    # 如果时间 < 15:00，返回数据库中的最新交易日
    if current_time < market_close_time:
        logger.info(f"当前时间 {current_time} < 15:00，返回数据库最新交易日: {latest_trading_date_from_db}")
        return latest_trading_date_from_db

    # 时间 >= 15:00，检查今天是否是交易日
    from src.db_operations import is_trading_day
    today_is_trading_day = is_trading_day(current_date)

    if today_is_trading_day:
        logger.info(f"当前时间 >= 15:00 且今天是交易日，返回今天: {current_date}")
        return current_date
    else:
        logger.info(f"当前时间 >= 15:00 且今天不是交易日，返回数据库最新交易日: {latest_trading_date_from_db}")
        return latest_trading_date_from_db


@app.get("/api/dashboard")
async def get_dashboard_data(queryDate: str = None):
    """
    获取仪表盘数据

    今日首板板块参数说明：
    - queryDate: 前端传递的查询日期（格式：YYYY-MM-DD）
    - 如果传递了 queryDate，使用该日期；否则使用 get_query_trading_date() 获取的日期
    - 前端日期选择功能通过此参数支持查看历史交易日数据
    """
    try:
        service = StockDataService()

        # 涨跌停统计：如果传递了 queryDate，则使用该日期查询历史数据
        limit_stats = await service.get_limit_stats(queryDate)
        # 连板梯队：直接使用涨跌停统计的 display_date
        continuous_limits_query_date = limit_stats.get('display_date') if limit_stats else None
        continuous_limits = await service.get_continuous_limits(continuous_limits_query_date)

        #        根据交易日规则获取查询日期
        # 1. 今日首板：优先使用前端传递的 queryDate，否则使用 get_query_trading_date() 获取的日期
        # 2. 市场整体状态总结和连板梯队：使用 get_market_summary_query_date()（15:00规则）
        today = queryDate if queryDate else get_query_trading_date()
        display_date = get_display_trade_date()

        # 市场整体状态总结和连板梯队：直接使用涨跌停统计的 display_date
        query_date_for_summary = continuous_limits_query_date
        market_summary = await service.get_market_summary(query_date_for_summary)
        continuous_analysis = await service.get_continuous_limit_analysis(query_date_for_summary)

        # 其他板块不使用日期选择器
        sectors = await service.get_sector_analysis()

        # 今日首板板块：判断是否应该使用临时表
        # 规则：
        # 1. 查询的日期 == get_query_trading_date()（今天应显示的交易日） 且 在交易时段：使用临时表
        # 2. 其他情况：使用正式表
        should_use_tmp_table_for_today = (today == get_query_trading_date() and is_in_trading_hours())
        first_limits_table = "first_limits_tmp" if should_use_tmp_table_for_today else "first_limits"
        activations_table = "topic_activations_tmp" if should_use_tmp_table_for_today else "topic_activations"
        topics_table = "first_limit_topics_tmp" if should_use_tmp_table_for_today else "first_limit_topics"

        # 首板数据：优先从数据库读取（包含 first_limit_id 和 stock_id）
        db_first_limits = db.get_first_limits_by_date(today, table=first_limits_table)

        first_limits = []
        if db_first_limits:
            for record in db_first_limits:
                first_limits.append({
                    'code': record['code'],
                    'name': record['name'],
                    'price': record['price'],
                    'change_percent': 10.0 if record['limit_type'] == '10%' else (20.0 if record['limit_type'] == '20%' else 30.0),
                    'first_time': record['first_time'] or '09:30',
                    'sector': record['sector'] or '综合',
                    'reason': record['reason'],
                    'stock_id': record['stock_id'],
                    'id': record['id']
                })
            first_limits.sort(key=lambda x: x['first_time'])

        # ========== 今日首板板块核心业务逻辑 ==========
        # 1. topic_activations 表：决定显示哪些题材卡片（按日期记录）
        # 2. first_limit_topics 表：记录首板与题材的关联（association_date = 首板与题材的真实活跃交易日）
        # 3. 题材卡片中的标的信息：根据 topic_id + association_date 从 first_limit_topics 表查询
        # 4. 关键点：只查询 topic_activations 表，不查询 topic_stock_relations（topic_stock_relations 是持久关联，由其他页面管理）
        # 5. today 是 get_query_trading_date() 返回的日期（开盘后应查看的交易日），用于查询今日首板相关数据
        # 6. 盘中数据：使用临时表；盘外数据：使用正式表

        today_topics = []
        try:
            # 核心步骤1：从 topic_activations 表获取 today 日期激活的题材（决定显示哪些题材卡片）
            activated_topics = db.get_activated_topics(today, table=activations_table)

            # 核心步骤2：对每个激活的题材，查询 today 日期关联的首板标的（从 first_limit_topics 表）
            for topic in activated_topics:
                # 获取该题材在 today 关联的首板标的（使用 association_date 过滤）
                stocks = db.get_topic_first_limits_by_association_date(topic['topic_id'], today, table=topics_table)

                if stocks:
                    stocks_data = []
                    for stock in stocks:
                        stocks_data.append({
                            'code': stock['code'],
                            'name': stock['name'],
                            'first_time': stock['first_time'] or '09:30',
                            'change_percent': 10.0 if stock['limit_type'] == '10%' else (20.0 if stock['limit_type'] == '20%' else 30.0),
                            'price': stock['price'],
                            'sector': stock['sector'] or '综合',
                            'reason': stock['reason'],
                            'stock_id': stock['stock_id'],
                            'id': stock['id']
                        })

                    today_topics.append({
                        'topic_id': topic['topic_id'],
                        'topic_name': topic['topic_name'],
                        'stocks': sorted(stocks_data, key=lambda x: x['first_time'])
                    })
                else:
                    # 空卡片（题材激活了但还没拖入首板）
                    today_topics.append({
                        'topic_id': topic['topic_id'],
                        'topic_name': topic['topic_name'],
                        'stocks': []
                    })
        except Exception as e:
            logger.error(f"获取今日题材数据失败: {e}", exc_info=True)
            today_topics = []

        return {
            "limit_stats": limit_stats,
            "continuous_limits": continuous_limits,
            "continuous_analysis": continuous_analysis,
            "sectors": sectors,
            "first_limits": first_limits,
            "today_topics": today_topics,
            "market_summary": market_summary,
            "display_date": display_date,
            "query_date": today,  # 今日首板板块使用的查询日期
            "is_trading": is_in_trading_hours(),  # 当前是否在交易时段
            "using_tmp_table": should_use_tmp_table_for_today,  # 是否使用临时表（基于查询日期判断）
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取仪表盘数据失败: {str(e)}", exc_info=True)
        return {
            "error": str(e),
            "message": "获取数据失败",
            "timestamp": datetime.now().isoformat()
        }


@app.get("/api/first-limits/export")
async def export_first_limits(
    date: str,
    topics: Optional[str] = None,
):
    """导出首板数据为 zip 文件

    文件：
    - 全部首板.txt（所有当日首板，每行一个代码，UTF-8）
    - <topic_name>.txt（每个选中题材，每行一个代码）

    参数：
    - date: YYYY-MM-DD（必填）
    - topics: 逗号分隔的题材名（可选；不传=全部题材+全部首板）
    """
    try:
        # 表选择逻辑（与 /api/dashboard 一致）
        should_use_tmp = (date == get_query_trading_date() and is_in_trading_hours())
        fl_table = "first_limits_tmp" if should_use_tmp else "first_limits"
        act_table = "topic_activations_tmp" if should_use_tmp else "topic_activations"
        flt_table = "first_limit_topics_tmp" if should_use_tmp else "first_limit_topics"

        # 1. 全部首板
        all_records = db.get_first_limits_by_date(date, table=fl_table)
        all_codes = sorted({r['code'] for r in all_records})

        # 2. 题材列表
        activated_topics = db.get_activated_topics(date, table=act_table)

        # 3. 题材过滤（None 表示全部）
        selected = None
        if topics:
            selected = set(t.strip() for t in topics.split(',') if t.strip())

        # 4. 打包 zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 全部首板（始终包含）
            zf.writestr("全部首板.txt", ("\n".join(all_codes) + "\n") if all_codes else "")

            # 每个题材
            for topic in activated_topics:
                if selected is not None and topic['topic_name'] not in selected:
                    continue
                stocks = db.get_topic_first_limits_by_association_date(
                    topic['topic_id'], date, table=flt_table
                )
                topic_codes = sorted({s['code'] for s in stocks})
                filename = f"{topic['topic_name']}.txt"
                zf.writestr(filename, ("\n".join(topic_codes) + "\n") if topic_codes else "")

        buf.seek(0)
        logger.info(f"导出首板 zip: date={date}, topics={topics or '全部'}, size={len(buf.getvalue())} bytes")
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="first_limits_{date}.zip"'},
        )
    except Exception as e:
        logger.error(f"导出首板失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "A股复盘工具",
        "version": "2.0.0",
        "data_source": "AkShare",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/test")
async def test_api():
    try:
        return {
            "message": "API测试成功",
            "is_trading": is_trading_day(),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "message": "API测试失败",
            "error": str(e)
        }

@app.get("/api/save-analysis")
async def save_analysis(analysis: dict):
    try:
        with open("data/analysis.json", "w", encoding='utf-8') as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)
        return {"success": True, "message": "分析保存成功"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/load-analysis")
async def load_analysis():
    try:
        if os.path.exists("data/analysis.json"):
            with open("data/analysis.json", "r", encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        return {"error": str(e)}

# ============= 新增：基于日期的API接口（替代day）=============

@app.get("/api/today")
async def get_today():
    """获取服务器当前日期"""
    try:
        today = datetime.now().date()
        return {
            "date": today.isoformat(),
            "weekday": today.weekday(),
            "weekday_str": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][today.weekday()]
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/query-trading-date")
async def get_query_trading_date_api():
    """获取查询首板数据时应该使用的交易日
    
    业务规则：
    - 9:15（开盘时间）前查上一交易日
    - 9:15及之后：如果当天是交易日，返回当天；否则返回最新交易日
    
    Returns:
        应该查询的交易日期字符串（格式：YYYY-MM-DD）
    """
    try:
        return {"date": get_query_trading_date()}
    except Exception as e:
        logger.error(f"获取查询交易日失败: {e}", exc_info=True)
        return {"error": str(e)}

@app.get("/api/recent-days")
async def get_recent_days(limit: int = 5):
    """获取最近N个交易日"""
    try:
        # 使用应该展示的交易日期作为基准（凌晨时为上一个交易日）
        display_trade_date = get_display_trade_date(query_existing_data=False)
        recent_trading_dates = get_recent_trading_days(limit, display_trade_date)

        return {"dates": recent_trading_dates, "count": len(recent_trading_dates)}
    except Exception as e:
        logger.error(f"获取最近日期失败: {e}", exc_info=True)
        return {"error": str(e), "dates": []}

@app.get("/api/rotation-records-by-date")
async def get_rotation_records_by_date(date: str):
    """根据日期查询题材记录"""
    try:
        # 验证日期格式
        try:
            date_obj = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return {"error": "Invalid date format, use YYYY-MM-DD", "date": date, "records": []}

        records = db.get_records_by_date(date_obj.isoformat())

        # 确保返回的记录中包含正确的日期信息
        for record in records:
            if 'date' not in record:
                record['date'] = date_obj.isoformat()

        # 计算相对天数（用于显示，但不作为查询依据）
        today = datetime.now().date()
        day_diff = (date_obj - today).days

        # 生成日期标签
        if day_diff == 0:
            date_label = "今日"
        elif day_diff == -1:
            date_label = "昨日"
        else:
            date_label = date_obj.strftime("%m-%d")

        return {
            "date": date_obj.isoformat(),
            "day": day_diff,  # 仅用于显示
            "date_label": date_label,
            "records": records,
            "count": len(records)
        }
    except Exception as e:
        logger.error(f"按日期获取题材记录失败: {e}", exc_info=True)
        return {"error": str(e), "date": date, "records": []}

@app.get("/api/next-trading-day")
async def get_next_trading_day(date: str):
    """获取指定日期之后的下一个交易日"""
    try:
        from datetime import timedelta

        # 验证日期格式
        try:
            current_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return {"error": "Invalid date format, use YYYY-MM-DD"}

        # 向后查找交易日（最多查找20天）
        for i in range(1, 21):
            next_date = current_date + timedelta(days=i)
            if next_date.weekday() >= 5:  # 跳过周末
                continue

            # 检查数据库是否有数据
            conn = db._get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*)
                FROM rotation_actives
                WHERE date = ? AND content IS NOT NULL AND content != ''
            ''', (next_date.isoformat(),))
            count = cursor.fetchone()[0]
            conn.close()

            # 如果有数据，返回此日期
            if count > 0:
                return {
                    "date": next_date.isoformat(),
                    "weekday": next_date.weekday(),
                    "weekday_str": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][next_date.weekday()]
                }

        # 如果找到最后都没有数据，返回最后一个工作日
        for i in range(1, 21):
            next_date = current_date + timedelta(days=i)
            if next_date.weekday() < 5:
                return {
                    "date": next_date.isoformat(),
                    "weekday": next_date.weekday(),
                    "weekday_str": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][next_date.weekday()]
                }

        return {"error": "No trading day found"}, {"date": None}

    except Exception as e:
        logger.error(f"获取下一个交易日失败: {e}", exc_info=True)
        return {"error": str(e), "date": None}

@app.get("/api/prev-trading-day")
async def get_prev_trading_day(date: str):
    """获取指定日期之前的上一个交易日"""
    try:
        from datetime import timedelta

        # 验证日期格式
        try:
            current_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return {"error": "Invalid date format, use YYYY-MM-DD"}

        # 向前查找交易日（最多查找20天）
        for i in range(1, 21):
            prev_date = current_date - timedelta(days=i)
            if prev_date.weekday() >= 5:  # 跳过周末
                continue

            # 检查数据库是否有数据
            conn = db._get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*)
                FROM rotation_actives
                WHERE date = ? AND content IS NOT NULL AND content != ''
            ''', (prev_date.isoformat(),))
            count = cursor.fetchone()[0]
            conn.close()

            # 如果有数据，返回此日期
            if count > 0:
                return {
                    "date": prev_date.isoformat(),
                    "weekday": prev_date.weekday(),
                    "weekday_str": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][prev_date.weekday()]
                }

        # 如果找到最后都没有数据，返回最后一个工作日
        for i in range(1, 21):
            prev_date = current_date - timedelta(days=i)
            if prev_date.weekday() < 5:
                return {
                    "date": prev_date.isoformat(),
                    "weekday": prev_date.weekday(),
                    "weekday_str": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][prev_date.weekday()]
                }

        return {"error": "No trading day found", "date": None}

    except Exception as e:
        logger.error(f"获取上一个交易日失败: {e}", exc_info=True)
        return {"error": str(e), "date": None}

@app.get("/api/init-today-if-needed")
async def init_today_if_needed():
    """如果是交易日且数据库没有数据，则初始化今天的记录"""
    try:
        today = datetime.now().date()

        # 检查是否是工作日
        if today.weekday() >= 5:  # 周六或周日
            return {
                "initialized": False,
                "reason": "Not a trading day",
                "today": today.isoformat()
            }

        # 检查是否已有数据
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*)
            FROM rotation_actives
            WHERE date = ?
        ''', (today.isoformat(),))
        count = cursor.fetchone()[0]
        conn.close()

        if count > 0:
            return {
                "initialized": False,
                "reason": "Already has data",
                "today": today.isoformat(),
                "record_count": count
            }

        # 如果没有数据，需要初始化（这里只是告知，不实际创建）
        return {
            "initialized": False,
            "needs_init": True,
            "reason": "Trading day but no data",
            "today": today.isoformat()
        }

    except Exception as e:
        logger.error(f"检查今天数据失败: {e}", exc_info=True)
        return {
            "error": str(e),
            "initialized": False
        }

@app.post("/api/add-first-limit-to-topic")
async def add_first_limit_to_topic(data: Dict = Body(...)):
    """将首板关联到题材

    首板板块API（重要！）：
    - 使用 stock_id 而非 first_limit_id，确保数据刷新后关联关系不被破坏
    - 优先使用前端传递的 queryDate，否则使用 get_query_trading_date() 获取当前查看的交易日期
    - association_date 必须与当前查看的交易日一致，确保数据正确性
    - create_time：用户操作时间（如周日操作，记录周日时间戳）
    - association_date：该首板与题材的真实活跃交易日（应等于当前查看的交易日）

    全局交易日规则：
    - 9:15（开盘时间）前：查看并操作上一个交易日
    - 9:15及之后：查看并操作当天（如果是交易日）
    - 如果当前不是交易日：查看并操作上一个交易日

    日期选择功能：
    - queryDate：前端日期选择器选中的交易日（格式：YYYY-MM-DD）
    - 支持：用户可以查看和修改历史交易日的数据

    Stock_ID 迁移说明：
    - 参数已从 first_limit_id 迁移到 stock_id
    - stock_id 是稳定的，不会因数据刷新而改变
    - 避免了孤儿引用问题
    
    全局交易日规则：
    - 9:15（开盘时间）前：查看并操作上一个交易日
    - 9:15及之后：查看并操作当天（如果是交易日）
    - 如果当前不是交易日：查看并操作上一个交易日

    日期选择功能：
    - queryDate：前端日期选择器选中的交易日（格式：YYYY-MM-DD）
    - 支持：用户可以查看和修改历史交易日的数据
    """
    try:
        stock_id = data.get('stock_id')
        topic_id = data.get('topic_id')
        date = data.get('date')
        query_date = data.get('queryDate')  # 前端传递的查询日期

        if not stock_id or not topic_id:
            return {"success": False, "message": "缺少必要参数"}

        # 优先使用前端传递的 queryDate，否则使用 get_query_trading_date() 获取正确的交易日（当前查看的交易日）
        # 确保关联日期与当前查看的交易日一致
        correct_association_date = query_date if query_date else get_query_trading_date()
        logger.info(f"关联首板到题材: stock_id={stock_id}, topic_id={topic_id}, association_date={correct_association_date}")

        success = db.add_first_limit_to_topic(stock_id, topic_id, date, correct_association_date)

        if success:
            return {"success": True, "message": "关联成功"}
        else:
            return {"success": False, "message": "关联失败"}
    except Exception as e:
        logger.error(f"添加首板-题材关联失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.get("/api/first-limit-topics")
async def get_first_limit_topics(first_limit_id: int):
    """获取首板关联的题材列表
    
    注意：此API使用 first_limit_id 作为查询参数
    Stock_ID 迁移说明：
    - 此API尚未迁移到 stock_id
    - 如需迁移，应改为接收 stock_id 参数
    - 或考虑废弃此API（前端目前未使用）
    """
    try:
        topics = db.get_first_limit_topics(first_limit_id)
        return {"topics": topics}
    except Exception as e:
        logger.error(f"获取首板题材列表失败: {e}", exc_info=True)
        return {"error": str(e), "topics": []}

@app.post("/api/remove-first-limit-topic")
async def remove_first_limit_topic(data: Dict = Body(...)):
    """移除首板-题材关联

    首板板块API（重要！）：
    - 优先使用前端传递的 queryDate，否则使用 get_query_trading_date() 获取当前查看的交易日期（开盘后应查看的交易日）
    - 删除 first_limit_topics 表中该日期的记录（按 association_date 过滤）
    - 根据前端参数决定是否删除 topic_stock_relations 表中的长期关联

    参数说明：
    - stock_id: 股票ID（已从 first_limit_id 迁移）
    - topic_id: 题材ID
    - remove_relation: 是否删除长期关联（默认false，不删除）
    - queryDate: 前端传递的查询日期（格式：YYYY-MM-DD）

    全局交易日规则：
    - 9:15（开盘时间）前：查看并操作上一个交易日
    - 9:15及之后：查看并操作当天（如果是交易日）
    - 如果当前不是交易日：查看并操作上一个交易日

    日期选择功能：
    - queryDate：前端日期选择器选中的交易日（格式：YYYY-MM-DD）
    - 支持：用户可以查看和修改历史交易日的数据
    """
    try:
        stock_id = data.get('stock_id')
        topic_id = data.get('topic_id')
        remove_relation = data.get('remove_relation', False)  # 默认不删除长期关联
        query_date = data.get('queryDate')  # 前端传递的查询日期

        if not stock_id or not topic_id:
            return {"success": False, "message": "缺少必要参数"}

        # 优先使用前端传递的 queryDate，否则使用 get_query_trading_date() 获取正确的交易日（当前查看的交易日）
        correct_association_date = query_date if query_date else get_query_trading_date()
        logger.info(f"移除首板-题材关联: stock_id={stock_id}, topic_id={topic_id}, association_date={correct_association_date}, remove_relation={remove_relation}")

        # 删除 first_limit_topics 记录
        success = db.remove_first_limit_topic(stock_id, topic_id, correct_association_date)

        # 如果用户选择删除长期关联，删除 topic_stock_relations 记录
        if success and remove_relation:
            logger.info(f"同时删除长期关联: topic_id={topic_id}, stock_id={stock_id}")
            db.remove_topic_stock_relation(topic_id, stock_id)

        if success:
            return {"success": True, "message": "移除成功"}
        else:
            return {"success": False, "message": "移除失败：未找到指定日期的关联记录"}
    except Exception as e:
        logger.error(f"移除首板-题材关联失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


# ============= 交易日相关API接口 =============

@app.post("/api/trading-days/init")
async def init_trading_days():
    """初始化交易日数据（从AkShare获取并保存最近3年的交易日）"""
    try:
        saved_count, trading_days = fetch_and_save_trading_days()

        if saved_count > 0:
            logger.info(f"初始化交易日成功，保存了 {saved_count} 个交易日")
            return {
                "success": True,
                "message": f"成功保存 {saved_count} 个交易日",
                "trading_days_count": saved_count,
                "first_day": trading_days[0] if trading_days else None,
                "last_day": trading_days[-1] if trading_days else None
            }
        else:
            logger.warning("初始化交易日失败，没有保存任何交易日")
            return {
                "success": False,
                "message": "未能保存任何交易日，可能已存在或数据源异常"
            }
    except Exception as e:
        logger.error(f"初始化交易日失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


@app.get("/api/trading-days/recent")
async def get_recent_trading_days_api(count: int = 5):
    """获取最近N个交易日

    Args:
        count: 获取的交易日数量，默认为5
    """
    try:
        recent_days = get_recent_trading_days(count)

        return {
            "success": True,
            "count": len(recent_days),
            "trading_days": recent_days
        }
    except Exception as e:
        logger.error(f"获取最近交易日失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "trading_days": []}


@app.post("/api/trading-days/ensure")
async def ensure_trading_day_exists_api(data: Dict = Body(...)):
    """确保指定交易日存在（如果不存在则添加）

    Args:
        data: {"date": "YYYY-MM-DD"}
    """
    try:
        date_str = data.get('date')

        if not date_str:
            return {"success": False, "message": "日期参数不能为空"}

        # 验证日期格式
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return {"success": False, "message": "日期格式不正确，请使用 YYYY-MM-DD 格式"}

        success = ensure_trading_day_exists(date_str)

        if success:
            return {"success": True, "message": f"交易日 {date_str} 已存在或成功添加"}
        else:
            return {"success": False, "message": "确保交易日存在失败"}
    except Exception as e:
        logger.error(f"确保交易日存在失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


@app.get("/api/trading-days/between")
async def get_trading_days_between_api(start_date: str, end_date: str):
    """获取两个日期之间的所有交易日

    Args:
        start_date: 开始日期（格式：YYYY-MM-DD）
        end_date: 结束日期（格式：YYYY-MM-DD）
    """
    try:
        # 验证日期格式
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError as e:
            return {"success": False, "message": f"日期格式不正确: {str(e)}"}

        trading_days = get_trading_days_between(start_date, end_date)

        return {
            "success": True,
            "count": len(trading_days),
            "start_date": start_date,
            "end_date": end_date,
            "trading_days": trading_days
        }
    except Exception as e:
        logger.error(f"获取日期范围内的交易日失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "trading_days": []}


@app.get("/api/trading-days/status")
async def get_trading_days_status():
    """获取交易日表的状态信息"""
    try:
        conn = db._get_connection()
        cursor = conn.cursor()

        # 统计记录数
        cursor.execute('SELECT COUNT(*) FROM trading_days')
        total_count = cursor.fetchone()[0]

        # 获取最早和最晚的日期
        cursor.execute('SELECT MIN(date), MAX(date) FROM trading_days')
        min_date, max_date = cursor.fetchone()

        # 获取最新的记录
        cursor.execute('SELECT date FROM trading_days ORDER BY date DESC LIMIT 5')
        recent_dates = [row[0] for row in cursor.fetchall()]

        conn.close()

        return {
            "success": True,
            "status": {
                "total_count": total_count,
                "min_date": min_date,
                "max_date": max_date,
                "recent_dates": recent_dates
            }
        }
    except Exception as e:
        logger.error(f"获取交易日状态失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

@app.get("/api/trading-days/all-available")
async def get_all_trading_days_before_today_api():
    """获取今天之前的所有交易日（用于日期选择器）

    今日首板板块API（重要！）：
    - 只返回今天及以前的交易日
    - 过滤掉未来日期（trading_days 表可能包含未来的交易日）
    - 按日期降序返回（最新的日期在前）

    Returns:
        {
            "success": true,
            "count": 100,
            "trading_days": ["2026-02-03", "2026-02-02", "2026-01-30", ...]
        }
    """
    try:
        trading_days = db.get_all_trading_days_before_today()

        return {
            "success": True,
            "count": len(trading_days),
            "trading_days": trading_days
        }
    except Exception as e:
        logger.error(f"获取交易日列表失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "trading_days": []}

@app.get("/api/dashboard-dates-status")
async def get_dashboard_dates_status():
    """获取仪表盘日期数据状态（用于标记日历）

    今日首板板块：
    - 返回所有有题材激活或首板数据的日期
    - 用于日历中标记哪些日期有数据
    - 返回格式：dates = {"2026-02-03": true, "2026-02-02": true, ...}
    """
    try:
        date_data = {}
        
        # 获取有题材激活的日期
        try:
            conn = db._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT DISTINCT activation_date
                FROM topic_activations
                ORDER BY activation_date DESC
            ''')
            activation_dates = [row[0] for row in cursor.fetchall()]
            
            for date in activation_dates:
                date_data[date] = True
            
            conn.close()
        except Exception as e:
            logger.warning(f"获取题材激活日期失败: {e}")
        
        # 获取有首板数据的日期
        try:
            conn = db._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT DISTINCT limit_date
                FROM first_limits
                ORDER BY limit_date DESC
            ''')
            first_limit_dates = [row[0] for row in cursor.fetchall()]
            
            for date in first_limit_dates:
                date_data[date] = True
            
            conn.close()
        except Exception as e:
            logger.warning(f"获取首板数据日期失败: {e}")

        return {
            "success": True,
            "count": len(date_data),
            "dates": date_data
        }
    except Exception as e:
        logger.error(f"获取仪表盘日期状态失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "dates": {}}


# 【已注释】人气榜相关API endpoints - 包含"人气、热度"相关的数据源管理
# 关联关系：
# - 前端调用：createPopularitySource() -> POST /api/popularity-sources/create (dashboard.html:5214)
# - 前端调用：deletePopularitySource() -> DELETE /api/popularity-sources/{source_id} (dashboard.html:5237)
# - 数据库操作：db_operations.get_popularity_sources() 等方法
# - 数据获取：data_acquisition.fetch_and_save_popularity_ranking() 调用 POST /api/popularity-stocks/save
# 注释原因：暂时隐藏人气榜功能，方便后期恢复
"""
@app.get("/api/popularity-sources")
async def get_popularity_sources():
    # 获取所有人气榜数据源（选项卡）
    try:
        sources = db.get_popularity_sources()
        return {"success": True, "sources": sources}
    except Exception as e:
        logger.error(f"获取人气榜数据源失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "sources": []}


@app.post("/api/popularity-sources/create")
async def create_popularity_source(data: Dict = Body(...)):
    # 创建人气榜数据源（选项卡）
    try:
        source_name = data.get('source_name', '')
        description = data.get('description', '')
        sort_order = data.get('sort_order', 0)

        if not source_name:
            return {"success": False, "message": "数据源名称不能为空"}

        source_id = db.create_popularity_source(source_name, description, sort_order)

        if source_id:
            return {"success": True, "message": "创建成功", "source_id": source_id}
        else:
            return {"success": False, "message": "创建失败"}
    except Exception as e:
        logger.error(f"创建人气榜数据源失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


@app.post("/api/popularity-sources/update")
async def update_popularity_source(data: Dict = Body(...)):
    # 更新人气榜数据源
    try:
        source_id = data.get('source_id')
        source_name = data.get('source_name')
        description = data.get('description')
        sort_order = data.get('sort_order')
        is_active = data.get('is_active')

        if not source_id:
            return {"success": False, "message": "数据源ID不能为空"}

        success = db.update_popularity_source(source_id, source_name, description, sort_order, is_active)

        if success:
            return {"success": True, "message": "更新成功"}
        else:
            return {"success": False, "message": "更新失败"}
    except Exception as e:
        logger.error(f"更新人气榜数据源失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


@app.delete("/api/popularity-sources/{source_id}")
async def delete_popularity_source(source_id: int):
    # 删除人气榜数据源（级联删除其下的所有标的记录）
    try:
        success = db.delete_popularity_source(source_id)

        if success:
            return {"success": True, "message": "删除成功"}
        else:
            return {"success": False, "message": "删除失败"}
    except Exception as e:
        logger.error(f"删除人气榜数据源失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


@app.get("/api/popularity-stocks")
async def get_popularity_stocks(source_id: int, trade_date: str):
    # 获取指定数据源和交易日的人气榜标的
    try:
        stocks = db.get_popularity_stocks(source_id, trade_date)
        return {"success": True, "stocks": stocks}
    except Exception as e:
        logger.error(f"获取人气榜标的失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "stocks": []}


@app.post("/api/popularity-stocks/save")
async def save_popularity_stocks(data: Dict = Body(...)):
    # 保存人气榜标的数据
    try:
        source_id = data.get('source_id')
        trade_date = data.get('trade_date')
        stocks = data.get('stocks', [])

        if not source_id or not trade_date:
            return {"success": False, "message": "数据源ID和交易日期不能为空"}

        count = db.save_popularity_stocks(source_id, trade_date, stocks)

        return {"success": True, "message": f"保存成功，共 {count} 条", "count": count}
    except Exception as e:
        logger.error(f"保存人气榜标的失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}
"""


@app.get("/api/amount-types")
async def get_amount_types():
    """获取所有成交额榜类型（选项卡）"""
    try:
        types = db.get_amount_types()
        return {"success": True, "types": types}
    except Exception as e:
        logger.error(f"获取成交额榜类型失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "types": []}


@app.get("/api/amount-stocks")
async def get_amount_stocks(type_id: int, trade_date: str):
    """获取指定类型和交易日的成交额榜标的"""
    try:
        stocks = db.get_amount_stocks(type_id, trade_date)
        return {"success": True, "stocks": stocks}
    except Exception as e:
        logger.error(f"获取成交额榜标的失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "stocks": []}


@app.post("/api/amount-stocks/save")
async def save_amount_stocks(data: Dict = Body(...)):
    """保存成交额榜标的数据"""
    try:
        type_id = data.get('type_id')
        trade_date = data.get('trade_date')
        stocks = data.get('stocks', [])
        check_final = data.get('check_final', True)

        if not type_id or not trade_date:
            return {"success": False, "message": "类型ID和交易日期不能为空"}

        count = db.save_amount_stocks(type_id, trade_date, stocks, check_final)

        if count == 0 and check_final:
            return {"success": False, "message": "数据已标记为final，不可修改"}
        else:
            return {"success": True, "message": f"保存成功，共 {count} 条", "count": count}
    except Exception as e:
        logger.error(f"保存成交额榜标的失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


@app.post("/api/amount-stocks/finalize")
async def finalize_amount_stocks(data: Dict = Body(...)):
    """将成交额榜数据标记为final（不可修改）"""
    try:
        type_id = data.get('type_id')
        trade_date = data.get('trade_date')

        if not type_id or not trade_date:
            return {"success": False, "message": "类型ID和交易日期不能为空"}

        success = db.set_amount_stocks_final(type_id, trade_date)

        if success:
            return {"success": True, "message": "标记成功"}
        else:
            return {"success": False, "message": "标记失败"}
    except Exception as e:
        logger.error(f"标记成交额榜为final失败: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


@app.get("/api/sector-high-count")
async def get_sector_high_count(date: str = None, onlyHistoryHigh: bool = False):
    """板块新高数量 - 统计各个行业今天新高的股票数量

    Args:
        date: 查询日期
        onlyHistoryHigh: True仅统计历史新高，False统计所有新高（半年、一年、历史）
    """
    try:
        conn = db._get_connection()
        cursor = conn.cursor()

        query_date = date if date else get_display_trade_date()

        if onlyHistoryHigh:
            # 仅统计历史新高
            source_filter = "('历史新高')"
        else:
            # 统计所有新高（半年、一年、历史）
            source_filter = "('半年新高', '一年新高', '历史新高')"

        cursor.execute(f'''
            SELECT 
                s.industry as industry_name,
                COUNT(DISTINCT ps.stock_id) as count
            FROM popularity_stocks ps
            JOIN stocks s ON ps.stock_id = s.stock_id
            JOIN popularity_sources psrc ON ps.source_id = psrc.source_id
            WHERE ps.trade_date = ?
            AND psrc.source_name IN {source_filter}
            AND (s.industry IS NOT NULL AND s.industry != '')
            GROUP BY s.industry
            ORDER BY count DESC
        ''', (query_date,))

        sectors = []
        for row in cursor.fetchall():
            sectors.append({
                'industry_name': row[0],
                'count': row[1]
            })

        conn.close()

        return {"success": True, "sectors": sectors, "date": query_date, "onlyHistoryHigh": onlyHistoryHigh}
    except Exception as e:
        logger.error(f"获取板块新高数量失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "sectors": []}


@app.get("/api/sector-high-stocks")
async def get_sector_high_stocks(date: str = None, onlyHistoryHigh: bool = False, industry: str = None):
    """板块新高股票列表 - 获取指定板块的新高股票列表

    Args:
        date: 查询日期
        onlyHistoryHigh: True仅统计历史新高，False统计所有新高（半年、一年、历史）
        industry: 板块名称
    """
    try:
        conn = db._get_connection()
        cursor = conn.cursor()

        query_date = date if date else get_display_trade_date()

        if onlyHistoryHigh:
            # 仅统计历史新高
            source_filter = "('历史新高')"
        else:
            # 统计所有新高（半年、一年、历史）
            source_filter = "('半年新高', '一年新高', '历史新高')"

        industry_filter = f"AND s.industry = ?" if industry else ""

        cursor.execute(f'''
            SELECT DISTINCT
                s.stock_code,
                s.stock_name,
                MAX(ps.change_percent) as change_percent
            FROM popularity_stocks ps
            JOIN stocks s ON ps.stock_id = s.stock_id
            JOIN popularity_sources psrc ON ps.source_id = psrc.source_id
            WHERE ps.trade_date = ?
            AND psrc.source_name IN {source_filter}
            AND (s.industry IS NOT NULL AND s.industry != '')
            {industry_filter}
            GROUP BY s.stock_code, s.stock_name
            ORDER BY change_percent DESC
        ''', (query_date, *([industry] if industry else [])))

        stocks = []
        for row in cursor.fetchall():
            stocks.append({
                'code': row[0],
                'name': row[1],
                'change_percent': row[2]
            })

        conn.close()

        return {"success": True, "stocks": stocks, "date": query_date, "industry": industry}
    except Exception as e:
        logger.error(f"获取板块新高股票列表失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "stocks": []}


@app.get("/api/hot-stocks")
async def get_hot_stocks_data(queryDate: str = None):
    """
    获取标的热度板块数据（支持日期选择器）

    日期规则：
    - 9:15之前：显示上一个交易日的数据（人气榜、全天成交额未更新，竞价成交额未开始）
    - 9:15之后：显示当前交易日的数据

    Args:
        queryDate: 查询日期（格式：YYYY-MM-DD），可选
    """
    try:
        now = datetime.now()
        current_date = now.date().strftime("%Y-%m-%d")
        current_time = now.time()
        market_open_time = time(9, 15)

        # 获取最新交易日
        latest_trading_date = get_latest_trading_date_from_db()
        if not latest_trading_date:
            latest_trading_date = get_last_trading_day()

        trade_date = None

        if queryDate:
            # 用户手动选择的日期
            trade_date = queryDate
        else:
            # 自动选择日期
            # 9:15之前显示上一个交易日
            if current_time < market_open_time:
                # 查询今天之前的最新交易日
                conn = db._get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT MAX(date)
                    FROM trading_days
                    WHERE date < ? AND is_active = 1
                ''', (current_date,))
                result = cursor.fetchone()
                conn.close()

                if result and result[0]:
                    trade_date = result[0]
                else:
                    trade_date = latest_trading_date
            else:
                from src.db_operations import is_trading_day
                if is_trading_day(current_date):
                    trade_date = current_date
                else:
                    trade_date = latest_trading_date

        # 返回标的热度数据
        result = await StockDataService().get_stock_popularity_data(trade_date)
        return result

    except Exception as e:
        logger.error(f"获取标的热度数据失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "trade_date": None, "popularity_sources": [], "popularity_data": [], "amount_types": [], "amount_data": []}


@app.get("/api/strong-stocks")
async def get_strong_stocks_data(queryDate: str = None, hotType: str = None):
    """
    获取强势股池数据

    Args:
        queryDate: 查询日期（格式：YYYY-MM-DD），可选
        hotType: 热度类型（如'60日新高'），可选，不传则返回所有类型
    """
    try:
        now = datetime.now()
        current_date = now.date().strftime("%Y-%m-%d")
        current_time = now.time()
        market_open_time = time(9, 15)

        # 获取最新交易日
        latest_trading_date = get_latest_trading_date_from_db()
        if not latest_trading_date:
            latest_trading_date = get_last_trading_day()

        trade_date = None

        if queryDate:
            trade_date = queryDate
        else:
            # 自动选择日期
            if current_time < market_open_time:
                conn = db._get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT MAX(date)
                    FROM trading_days
                    WHERE date < ? AND is_active = 1
                ''', (current_date,))
                result = cursor.fetchone()
                conn.close()

                if result and result[0]:
                    trade_date = result[0]
                else:
                    trade_date = latest_trading_date
            else:
                from src.db_operations import is_trading_day
                if is_trading_day(current_date):
                    trade_date = current_date
                else:
                    trade_date = latest_trading_date

        # 获取强势股热度类型
        hot_types = db.get_strong_stock_types()

        # 获取强势股数据
        strong_stocks = db.get_strong_stocks(hot_type=hotType, trade_date=trade_date)

        return {
            "success": True,
            "trade_date": trade_date,
            "hot_types": hot_types,
            "strong_stocks": strong_stocks
        }

    except Exception as e:
        logger.error(f"获取强势股数据失败: {e}", exc_info=True)
        return {"success": False, "message": str(e), "trade_date": None, "hot_types": [], "strong_stocks": []}


@app.get("/api/has-strong-stocks")
async def has_strong_stocks(queryDate: str = None):
    """
    检查数据库中是否存在强势标的（首板）数据

    Args:
        queryDate: 查询日期（格式：YYYY-MM-DD），可选

    Returns:
        has_data: 是否存在强势标的数据
    """
    try:
        # 获取查询日期
        if not queryDate:
            latest_trading_date = get_latest_trading_date_from_db()
            if not latest_trading_date:
                latest_trading_date = get_last_trading_day()
            queryDate = latest_trading_date

        # 检查强势标的表是否有数据
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*)
            FROM strong_stocks
            WHERE trade_date = ?
        ''', (queryDate,))
        count = cursor.fetchone()[0]
        conn.close()

        return {
            "success": True,
            "has_data": count > 0,
            "count": count,
            "query_date": queryDate
        }

    except Exception as e:
        logger.error(f"检查强势标的存在失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "has_data": False,
            "query_date": queryDate
        }


@app.get("/api/check-primary-filtered")
async def check_primary_filtered(queryDate: str = None):
    """
    检查根据初选规则筛选后，是否有符合条件的标的

    Args:
        queryDate: 查询日期（格式：YYYY-MM-DD），可选

    Returns:
        has_results: 是否有符合初选规则的标的
    """
    try:
        # 获取查询日期
        if not queryDate:
            latest_trading_date = get_latest_trading_date_from_db()
            if not latest_trading_date:
                latest_trading_date = get_last_trading_day()
            queryDate = latest_trading_date

        # 从配置文件读取初选规则
        config_file = 'config/trend_filter_rules.json'
        config = {}

        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
        else:
            # 使用默认配置
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
        def get_field_value(field_config, key, default):
            if field_config is None:
                return default
            return field_config.get(key, default)

        price_config = config.get('price', {})
        change_percent_config = config.get('change_percent', {})
        amount_config = config.get('amount', {})
        turnover_config = config.get('turnover', {})
        volume_ratio_config = config.get('volume_ratio', {})
        continuous_limit_config = config.get('continuous_limit', {})

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

        # 构建SQL查询
        conditions = ['ss.trade_date = ?']
        params = [queryDate]

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

        sql = f'''
            SELECT COUNT(*)
            FROM strong_stocks ss
            JOIN stocks s ON ss.stock_id = s.stock_id
            WHERE {where_clause}
        '''

        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        count = cursor.fetchone()[0]
        conn.close()

        return {
            "success": True,
            "has_results": count > 0,
            "count": count,
            "query_date": queryDate
        }

    except Exception as e:
        logger.error(f"检查初选结果失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "has_results": False,
            "query_date": queryDate
        }


@app.post("/api/hot-stocks/refresh")
async def refresh_hot_stocks_data(targetDate: str = Body(..., embed=True, description="要刷新的目标日期")):
    # 【已注释完整文档说明】手动刷新标的热度数据（用户主动触发）
    # 当用户点击刷新按钮时调用此API，
    # 会使用用户通过日期选择器选择的日期获取标的热度数据并保存到数据库
    #
    # 日期规则：
    # 1. 竞价成交额：仅在交易日的9:15~9:30之间可刷新
    # 2. 全天成交额：仅在交易日的15:00后可刷新（往期数据随时可刷新）
    # 3. 【已注释】人气榜：仅在交易日的15:00后可刷新（往期数据随时可刷新）
    #
    # Args:
    #     targetDate: 用户通过日期选择器选择的目标日期（格式：YYYY-MM-DD）
    try:
        from src.data_acquisition import DataAcquisitionService
        from datetime import datetime, time

        logger.info("手动刷新标的热度数据开始")
        logger.info(f"用户选择的刷新日期: {targetDate}")

        now = datetime.now()
        current_time = now.time()
        current_date = now.strftime("%Y-%m-%d")
        auction_start = time(9, 15)
        auction_end = time(9, 30)
        market_close = time(15, 0)

        from src.db_operations import is_trading_day
        target_is_trading_day = is_trading_day(targetDate)
        today_is_trading_day = is_trading_day(current_date)
        is_today = targetDate == current_date

        service = DataAcquisitionService()

        results = {
            "success": True,
            "message": "",
            "target_date": targetDate,
            "auction_amount": {"success": False, "message": "", "count": 0},
            "full_day_amount": {"success": False, "message": "", "count": 0},
            "popularity_data": {}
        }

        if is_today and target_is_trading_day:
            if auction_start <= current_time <= auction_end:
                amount_result = service.fetch_and_save_amount_ranking("竞价成交额", targetDate)
                results["auction_amount"]["success"] = amount_result.get("success", False)
                results["auction_amount"]["message"] = amount_result.get("message", "")
                results["auction_amount"]["count"] = amount_result.get("record_count", 0)
            else:
                if current_time > auction_end:
                    results["auction_amount"]["message"] = "竞价数据仅在9:15-9:30之间可获取，当前已过交易时间"
                else:
                    results["auction_amount"]["message"] = "未到竞价数据获取时间（9:15-9:30）"
        else:
            results["auction_amount"]["message"] = "往期数据无法刷新竞价成交额"

        if is_today and target_is_trading_day:
            if current_time >= market_close:
                amount_result = service.fetch_and_save_amount_ranking("全天成交额", targetDate)
                results["full_day_amount"]["success"] = amount_result.get("success", False)
                results["full_day_amount"]["message"] = amount_result.get("message", "")
                results["full_day_amount"]["count"] = amount_result.get("record_count", 0)
            else:
                results["full_day_amount"]["message"] = "全天成交额数据需在15:00后才能刷新"
        else:
            amount_result = service.fetch_and_save_amount_ranking("全天成交额", targetDate)
            results["full_day_amount"]["success"] = amount_result.get("success", False)
            results["full_day_amount"]["message"] = amount_result.get("message", "")
            results["full_day_amount"]["count"] = amount_result.get("record_count", 0)

        # 人气榜数据刷新逻辑
        # 关联关系：
        # - 调用 db_operations.get_popularity_sources() (line 2253)
        # - 调用 data_acquisition.fetch_and_save_popularity_ranking() (line 639)
        # - 保存到 popularity_stocks 表
        # - 与前端 dashboard.html 的人气榜功能关联
        # 过滤说明：只处理"新高榜"数据源（半年新高、一年新高、历史新高）
        popularity_sources = db.get_popularity_sources()
        allowed_sources = ['半年新高', '一年新高', '历史新高']
        for source in popularity_sources:
            source_name = source['source_name']
            if source_name not in allowed_sources:
                continue

            if is_today and target_is_trading_day:
                if current_time >= market_close:
                    popularity_result = service.fetch_and_save_popularity_ranking(source_name, targetDate)
                    results["popularity_data"][source_name] = {
                        "success": popularity_result.get("success", False),
                        "message": popularity_result.get("message", ""),
                        "count": popularity_result.get("record_count", 0)
                    }
                else:
                    results["popularity_data"][source_name] = {
                        "success": False,
                        "message": "人气榜数据需在15:00后才能刷新",
                        "count": 0
                    }
            else:
                popularity_result = service.fetch_and_save_popularity_ranking(source_name, targetDate)
                results["popularity_data"][source_name] = {
                    "success": popularity_result.get("success", False),
                    "message": popularity_result.get("message", ""),
                    "count": popularity_result.get("record_count", 0)
                }

        # 强势股池数据刷新逻辑
        logger.info("开始刷新强势股池数据...")
        strong_result = await service.fetch_and_save_strong_stocks(targetDate)
        results["strong_stocks"] = {
            "success": strong_result.get("success", False),
            "message": strong_result.get("message", ""),
            "count": strong_result.get("total_count", 0),
            "hot_types": strong_result.get("hot_types", [])
        }

        message_parts = []
        message_parts.append(f"目标日期: {targetDate}")

        if strong_result.get("success"):
            message_parts.append(f"强势股池: {strong_result.get('total_count', 0)}条")
        else:
            message_parts.append(f"强势股池: {strong_result.get('message', '未知错误')}")

        success_count = sum(1 for v in results["popularity_data"].values() if v["success"]) if results.get("popularity_data") else 0
        if results["full_day_amount"]["success"]:
            success_count += 1

        if results["auction_amount"]["success"]:
            message_parts.append(f"竞价成交额: {results['auction_amount']['count']}条")
        else:
            message_parts.append(f"竞价成交额: {results['auction_amount']['message']}")

        if results["full_day_amount"]["success"]:
            message_parts.append(f"全天成交额: {results['full_day_amount']['count']}条")
        else:
            message_parts.append(f"全天成交额: {results['full_day_amount']['message']}")

        # 人气榜消息处理
        if success_count > 0:
            message_parts.append(f"人气榜: {success_count}个数据源成功")
        else:
            popularity_messages = []
            for source_name, data in results["popularity_data"].items():
                if not data["success"]:
                    popularity_messages.append(f"{source_name}: {data['message']}")
            if popularity_messages:
                message_parts.append(f"人气榜: {', '.join(popularity_messages)}")

        results["message"] = "\n".join(message_parts)

        logger.info(f"手动刷新标的热度数据完成: {results}")
        return results

    except Exception as e:
        logger.error(f"手动刷新标的热度数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# 环境配置管理
class SettingsConfig(BaseModel):
    mairui_licence: Optional[str] = None
    mairui_base_url: Optional[str] = None


class RangeFilterConfig(BaseModel):
    enabled: bool = True
    min_enabled: bool = True
    max_enabled: bool = False
    min: Optional[float] = None
    max: Optional[float] = None


class ContinuousLimitConfig(BaseModel):
    enabled: bool = True
    mode: str = 'require'
    min_days: int = 2


class TrendFilterRules(BaseModel):
    price: RangeFilterConfig = RangeFilterConfig()
    change_percent: RangeFilterConfig = RangeFilterConfig()
    amount: RangeFilterConfig = RangeFilterConfig()
    turnover: RangeFilterConfig = RangeFilterConfig()
    volume_ratio: RangeFilterConfig = RangeFilterConfig()
    continuous_limit: ContinuousLimitConfig = ContinuousLimitConfig()
    max_count: int = 20


@app.get("/api/trend-filter-rules")
async def get_trend_filter_rules():
    """获取趋势标的初选规则配置"""
    try:
        config_file = 'config/trend_filter_rules.json'
        if not os.path.exists(config_file):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        return {
            "success": True,
            "config": config
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取初选规则配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取配置失败: {str(e)}")


@app.post("/api/trend-filter-rules")
async def save_trend_filter_rules(rules: TrendFilterRules):
    """保存趋势标的初选规则配置"""
    try:
        config_file = 'config/trend_filter_rules.json'
        config_dir = os.path.dirname(config_file)

        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir, exist_ok=True)

        config_dict = rules.model_dump()
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config_dict, f, ensure_ascii=False, indent=4)

        logger.info(f"初选规则配置已保存: {config_dict}")
        return {
            "success": True,
            "message": "配置已保存"
        }
    except Exception as e:
        logger.error(f"保存初选规则配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存配置失败: {str(e)}")


@app.post("/api/trend-filter-rules/reset")
async def reset_trend_filter_rules():
    """重置趋势标的初选规则为默认值"""
    try:
        default_config_file = 'config/trend_filter_rules.default.json'

        if not os.path.exists(default_config_file):
            raise HTTPException(status_code=404, detail="默认配置文件不存在")

        with open(default_config_file, 'r', encoding='utf-8') as f:
            default_config = json.load(f)

        logger.info(f"已加载默认趋势标筛选配置: {default_config}")

        return {
            "success": True,
            "config": default_config
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重置初选规则失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"重置配置失败: {str(e)}")


@app.get("/api/settings")
async def get_settings():
    """获取环境配置"""
    try:
        return {
            "mairui_licence": os.getenv('MAIRUI_LICENCE', ''),
            "mairui_base_url": os.getenv('MAIRUI_BASE_URL', 'https://api.mairuiapi.com')
        }
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置失败: {str(e)}")


@app.post("/api/settings")
async def save_settings(config: SettingsConfig):
    """保存环境配置到.env文件"""
    try:
        env_file = '.env.1'
        env_lines = []

        # 读取现有的.env文件内容（如果存在）
        if os.path.exists(env_file):
            with open(env_file, 'r', encoding='utf-8') as f:
                env_lines = f.readlines()

        # 创建一个字典来存储当前的配置
        env_dict = {}
        for line in env_lines:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_dict[key.strip()] = value.strip()

        # 更新配置
        if config.mairui_licence is not None:
            env_dict['MAIRUI_LICENCE'] = config.mairui_licence
        if config.mairui_base_url is not None:
            env_dict['MAIRUI_BASE_URL'] = config.mairui_base_url

        # 写入.env文件
        with open(env_file, 'w', encoding='utf-8') as f:
            for key, value in env_dict.items():
                f.write(f"{key}={value}\n")

        # 更新当前进程的环境变量
        if config.mairui_licence is not None:
            os.environ['MAIRUI_LICENCE'] = config.mairui_licence
        if config.mairui_base_url is not None:
            os.environ['MAIRUI_BASE_URL'] = config.mairui_base_url

        logger.info(f"环境配置已保存: MAIRUI_LICENCE={'***' if config.mairui_licence else '未设置'}, MAIRUI_BASE_URL={config.mairui_base_url}")

        return {
            "success": True,
            "message": "配置保存成功，重启服务后生效"
        }
    except Exception as e:
        logger.error(f"保存配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存配置失败: {str(e)}")


def _check_has_kline_data(trade_date: str) -> bool:
    """
    检查某个日期是否有K线数据（用于判断API是否成功但评分过低）

    Args:
        trade_date: 交易日期（格式YYYY-MM-DD）

    Returns:
        bool: 是否有K线数据
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM stock_daily_data WHERE trade_date >= ?', (trade_date,))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as e:
        logger.error(f"检查K线数据失败: {e}")
        return False


# ========== 趋势标的相关API（独立模块，不改造原有功能）==========

@app.get("/api/trend-stocks")
async def get_trend_stocks_data(
    queryDate: str = None,
    force_refresh: str = None
):
    """
    获取趋势标的列表

    优先从数据库读取，无数据时才实时计算
    支持 force_refresh 参数强制刷新（异步版本）
    配置从配置文件读取

    Args:
        queryDate: 查询日期（格式：YYYY-MM-DD），可选
        force_refresh: 是否强制刷新（跳过数据库直接计算），"true"|"false"
    """
    try:
        logger.info(f"[API] 收到趋势标的请求: queryDate={queryDate}, force_refresh={force_refresh}")

        # 获取查询日期
        if not queryDate:
            latest_trading_date = get_latest_trading_date_from_db()
            if not latest_trading_date:
                latest_trading_date = get_last_trading_day()
            queryDate = latest_trading_date
        elif queryDate in ['今日', 'today']:
            # 处理"今日"参数
            latest_trading_date = get_latest_trading_date_from_db()
            if not latest_trading_date:
                latest_trading_date = get_last_trading_day()
            queryDate = latest_trading_date
            logger.info(f"queryDate为'{queryDate}'，已转换为最新交易日: {queryDate}")

        # 从配置文件读取筛选规则
        config_file = 'config/trend_filter_rules.json'
        config = {}

        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            logger.info(f"从配置文件读取初选规则: {config_file}")
        else:
            # 使用默认配置
            config = {
                'price': {'enabled': True, 'min_enabled': True, 'max_enabled': False, 'min': 10.0, 'max': None},
                'change_percent': {'enabled': True, 'min_enabled': True, 'max_enabled': False, 'min': 5.0, 'max': None},
                'amount': {'enabled': True, 'min_enabled': True, 'max_enabled': False, 'min': 300.0, 'max': None},
                'turnover': {'enabled': True, 'min_enabled': True, 'max_enabled': False, 'min': 2.0, 'max': None},
                'volume_ratio': {'enabled': True, 'min_enabled': True, 'max_enabled': False, 'min': 1.0, 'max': None},
                'continuous_limit': {'enabled': True, 'mode': 'require', 'min_days': 2},
                'max_count': 20
            }
            logger.warning(f"配置文件不存在，使用默认配置")

        # 调用独立的趋势票分析模块
        from src.trend_analysis import TrendStockAnalyzer

        analyzer = TrendStockAnalyzer()

        # 判断是否强制刷新
        is_force_refresh = force_refresh and force_refresh.lower() in ['true', '1', 'yes']

        has_api_success = False

        logger.info(f"[API] 开始处理趋势标请求: queryDate={queryDate}, is_force_refresh={is_force_refresh}")

        if is_force_refresh:
            logger.info(f"[API] 强制刷新趋势票数据 ({queryDate})，跳过数据库")
            trend_stocks = await analyzer.get_trend_stocks_by_date(queryDate, config=config)
            source = "实时计算"
        else:
            # 优先从数据库读取
            trend_stocks = analyzer.get_saved_trend_stocks(queryDate)

            if not trend_stocks or len(trend_stocks) == 0:
                # 检查是否有K线数据（说明API成功但评分过低）
                has_kline_data = _check_has_kline_data(queryDate)
                if has_kline_data:
                    has_api_success = True
                    logger.info(f"[API] 日期 {queryDate} 有K线数据但无趋势标（API成功但评分均<50分）")

                logger.info(f"[API] 数据库中无趋势票数据 ({queryDate})，开始实时计算...")
                # 实时计算（异步执行，不阻塞其他请求）
                trend_stocks = await analyzer.get_trend_stocks_by_date(queryDate, config=config)
                source = "实时计算"
            else:
                logger.info(f"[API] 从数据库读取到 {len(trend_stocks)} 只趋势票")
                source = "数据库"

        logger.info(f"[API] 趋势标请求完成: queryDate={queryDate}, source={source}, 数量={len(trend_stocks)}")

        return {
            "success": True,
            "queryDate": queryDate,
            "trend_stocks": trend_stocks,
            "source": source,
            "has_api_success": has_api_success or len(trend_stocks) > 0
        }
    except Exception as e:
        logger.error(f"[API] 获取趋势标的失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "queryDate": queryDate,
            "trend_stocks": []
        }


@app.get("/api/trend-stocks/history")
async def get_trend_stocks_history(days: int = 24):
    """
    获取近N个交易日的趋势标历史数据

    统计每个标的所有入选记录，按入选次数和最近入选日期分类

    Args:
        days: 查询天数（默认24），包含查询日期
    """
    try:
        from datetime import datetime

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        logger.info(f"开始获取历史趋势标数据（最近{days}个交易日）")

        # 历史趋势标查询使用正式表
        first_limits_table = "first_limits"

        # 获取最近N个交易日
        cursor.execute('''
            SELECT DISTINCT trade_date
            FROM trend_stocks
            ORDER BY trade_date DESC
            LIMIT ?
        ''', (days,))

        trading_dates = [row[0] for row in cursor.fetchall()]

        if not trading_dates:
            conn.close()
            return {
                "success": False,
                "message": "暂无趋势标数据",
                "data": {
                    "by_count": {},
                    "by_date": {}
                }
            }

        date_placeholders = ','.join(['?' for _ in trading_dates])

        # 查询所有趋势标数据
        query = f'''
            SELECT
                s.stock_code,
                s.stock_name,
                ts.trade_date,
                ts.total_score,
                ts.change_pct_60d
            FROM trend_stocks ts
            JOIN stocks s ON ts.stock_id = s.stock_id
            WHERE ts.trade_date IN ({date_placeholders})
            ORDER BY ts.trade_date DESC
        '''

        cursor.execute(query, trading_dates)
        trend_rows = cursor.fetchall()

        logger.info(f"历史趋势标: 获取到 {len(trend_rows)} 条记录")

        # 查询所有涨停数据
        query_limits = f'''
            SELECT
                s.stock_code,
                COUNT(*) as limit_count
            FROM {first_limits_table} fl
            JOIN stocks s ON fl.stock_id = s.stock_id
            WHERE fl.limit_date IN ({date_placeholders})
            GROUP BY s.stock_id
        '''

        cursor.execute(query_limits, trading_dates)
        limit_rows = cursor.fetchall()

        # 构建涨停次数字典
        limit_count_map = {}
        for code, count in limit_rows:
            limit_count_map[code] = count

        logger.info(f"历史趋势标: 涨停数据 {len(limit_count_map)} 只股票")

        # 查询所有新高数据
        query_highs = f'''
            SELECT
                s.stock_code,
                COUNT(*) as new_high_count
            FROM popularity_stocks ps
            JOIN stocks s ON ps.stock_id = s.stock_id
            WHERE ps.trade_date IN ({date_placeholders})
              AND ps.source_id = 9
            GROUP BY s.stock_id
        '''

        cursor.execute(query_highs, trading_dates)
        high_rows = cursor.fetchall()

        # 构建新高次数字典
        new_high_count_map = {}
        for code, count in high_rows:
            new_high_count_map[code] = count

        logger.info(f"历史趋势标: 新高数据 {len(new_high_count_map)} 只股票")

        conn.close()

        # 第一步：按股票整理入选记录（包含完整统计数据）
        stock_selections = {}

        for row in trend_rows:
            code, name, date, score, change_pct_60d = row

            # 只处理有效记录（score > 0）
            if score <= 0:
                continue

            # 处理 change_pct_60d（可能是二进制数据）
            try:
                if isinstance(change_pct_60d, bytes):
                    change_pct_60d = struct.unpack('d', change_pct_60d)[0]
            except:
                change_pct_60d = None

            if code not in stock_selections:
                stock_selections[code] = {
                    "code": code,
                    "name": name,
                    "selection_dates": [],
                    "scores": [],
                    "changes_60d": [],
                    "limit_count": limit_count_map.get(code, 0),
                    "new_high_count": new_high_count_map.get(code, 0)
                }

            stock_selections[code]["selection_dates"].append(date)
            stock_selections[code]["scores"].append(score)
            stock_selections[code]["changes_60d"].append(change_pct_60d)

        # 第二步：按入选次数分类
        by_count = {}

        for code, data in stock_selections.items():
            count = len(data["selection_dates"])

            # 计算统计数据
            selection_count = count
            recent_date = max(data["selection_dates"]) if data["selection_dates"] else None
            recent_score = None
            latest_60d_change = None

            # 找到最新日期的得分和涨幅
            for i, date in enumerate(data["selection_dates"]):
                if date == recent_date:
                    recent_score = data["scores"][i]
                    latest_60d_change = data["changes_60d"][i]
                    break

            if count not in by_count:
                by_count[count] = []

            by_count[count].append({
                "code": data["code"],
                "name": data["name"],
                "selection_count": selection_count,
                "selection_dates": data["selection_dates"],
                "scores": data["scores"],
                "changes_60d": data["changes_60d"],
                "recent_score": recent_score,
                "latest_60d_change": latest_60d_change,
                "limit_count": data["limit_count"],
                "new_high_count": data["new_high_count"]
            })

        # 第三步：按日期分类（每只股票只在最近入选日期显示）
        by_date = {}
        for code, data in stock_selections.items():
            # 找到最近入选日期
            recent_date = max(data["selection_dates"]) if data["selection_dates"] else None

            if recent_date:
                if recent_date not in by_date:
                    by_date[recent_date] = []

                # 找到对应日期的索引
                recent_index = data["selection_dates"].index(recent_date)

                by_date[recent_date].append({
                    "code": data["code"],
                    "name": data["name"],
                    "recent_score": data["scores"][recent_index],
                    "latest_60d_change": data["changes_60d"][recent_index],
                    "limit_count": data["limit_count"],
                    "new_high_count": data["new_high_count"]
                })

        # 按次数从高到低排序
        by_count_sorted = {}
        for count in sorted(by_count.keys(), reverse=True):
            by_count_sorted[count] = by_count[count]

        logger.info(f"历史趋势标: 完成，共{len(by_count_sorted)}个入选次数分组，{len(by_date)}个日期，{len(stock_selections)}只股票")

        return {
            "success": True,
            "data": {
                "by_count": by_count_sorted,
                "by_date": by_date
            }
        }
    except Exception as e:
        logger.error(f"获取趋势标历史数据失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "data": {
                "by_count": {},
                "by_date": {}
            }
        }


@app.post("/api/trend-stocks/calculate")
async def calculate_trend_stocks_data(targetDate: str = Body(..., embed=True, description="要计算的目标日期")):
    """
    计算并保存趋势票数据（收盘后执行）

    Args:
        targetDate: 要计算的目标日期（格式：YYYY-MM-DD）
    """
    try:
        from src.trend_analysis import TrendStockAnalyzer
        
        analyzer = TrendStockAnalyzer()
        count, message = analyzer.calculate_and_save_trend_stocks(targetDate)
        
        return {
            "success": True,
            "message": message,
            "targetDate": targetDate,
            "count": count
        }
    except Exception as e:
        logger.error(f"计算趋势票失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "targetDate": targetDate,
            "count": 0
        }


@app.get("/api/stock-kline")
async def get_stock_kline(code: str, days: int = 90):
    """
    获取股票K线数据（带均线）
    
    Args:
        code: 股票代码
        days: 获取天数（默认90天）
    """
    try:
        import numpy as np
        from src.trend_analysis import TrendStockAnalyzer
        
        analyzer = TrendStockAnalyzer()
        
        # 获取K线数据
        df = analyzer.fetch_stock_kline(code, days=days)
        
        if df is None or len(df) == 0:
            return {
                "success": False,
                "message": "无数据",
                "klines": []
            }
        
        # 计算指标
        df = analyzer._calculate_indicators(df)
        
        # 转换为JSON（处理NaN值）
        klines = []
        for _, row in df.iterrows():
            klines.append({
                "date": row['date'],
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": float(row['volume']),
                "changePercent": float(row['change_pct']),
                "ma5": float(row['ma5']) if not np.isnan(row['ma5']) else None,
                "ma10": float(row['ma10']) if not np.isnan(row['ma10']) else None,
                "ma20": float(row['ma20']) if not np.isnan(row['ma20']) else None,
                "ma60": float(row['ma60']) if not np.isnan(row['ma60']) else None
            })
        
        return {
            "success": True,
            "klines": klines
        }
    except Exception as e:
        logger.error(f"获取K线失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "klines": []
        }


@app.post("/api/trend-scan/run")
async def run_trend_scan(config: dict = Body(...)):
    """
    运行全市场趋势票批量筛选
    
    Args:
        config: 配置字典
            - run_count: 运行数量（"all" 或具体数量）
            - exclude_kcb: 排除科创板
            - exclude_cyb: 排除创业板
            - exclude_st: 排除ST
    """
    try:
        import subprocess
        import os
        import sys
        import json
        import psutil
        from datetime import datetime
        
        # 检查是否有任务正在运行
        progress_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'trend_screen_progress.json')
        
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    progress = json.load(f)
                
                pid = progress.get('pid')
                status = progress.get('status')
                
                # 如果状态不是completed或error，检查进程是否还在运行
                if status in ('running', 'starting') and pid:
                    try:
                        proc = psutil.Process(pid)
                        if proc.is_running():
                            cmd = proc.cmdline()
                            if any('batch_screen_trend_stocks.py' in item for item in cmd):
                                logger.warning(f"已有任务正在运行(PID: {pid})，拒绝启动新任务")
                                return {
                                    "success": False,
                                    "message": f"已有任务正在运行中（PID: {pid}），请等待当前任务完成后再启动新任务。\n\n当前进度: {progress.get('processed_count', 0)}/{progress.get('total_count', 0)}"
                                }
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
            except Exception as e:
                logger.warning(f"检查运行状态失败: {e}，继续启动任务")
        
        run_count = config.get('run_count', 'all')
        exclude_kcb = config.get('exclude_kcb', True)
        exclude_cyb = config.get('exclude_cyb', True)
        exclude_st = config.get('exclude_st', True)
        
        # 准备环境变量
        env = os.environ.copy()
        env['RUN_COUNT'] = str(run_count)
        env['EXCLUDE_KCB'] = str(exclude_kcb)
        env['EXCLUDE_CYB'] = str(exclude_cyb)
        env['EXCLUDE_ST'] = str(exclude_st)
        env['TASK_PID'] = ''
        
        # 启动批量筛选脚本
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'batch_screen_trend_stocks.py')
        
        # 使用subprocess.Popen异步运行
        process = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        logger.info(f"已启动趋势票批量筛选，进程ID: {process.pid}")
        
        # 初始化进度文件，记录PID，防止"秒结束"
        try:
            init_progress = {
                'trade_date': 'initializing',
                'processed_count': 0,
                'total_count': int(run_count) if run_count != 'all' else 3360,
                'processed_codes': [],
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'pid': process.pid,
                'status': 'starting'
            }
            os.makedirs(os.path.dirname(progress_file), exist_ok=True)
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(init_progress, f, ensure_ascii=False, indent=2)
            logger.info(f"初始化进度文件成功，PID: {process.pid}")
        except Exception as e:
            logger.warning(f"初始化进度文件失败: {e}，不影响任务启动")
        
        return {
            "success": True,
            "message": f"批量筛选已启动，进程ID: {process.pid}",
            "pid": process.pid
        }
    except Exception as e:
        logger.error(f"启动趋势票筛选失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e)
        }


@app.get("/api/trend-scan/progress")
async def get_trend_scan_progress():
    """
    查询趋势票筛选进度
    """
    try:
        import os
        import json
        import psutil
        from datetime import datetime
        
        progress_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'trend_screen_progress.json')
        
        if not os.path.exists(progress_file):
            return {
                "success": True,
                "running": False
            }
        
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
        
        # 检查PID对应的进程是否还在运行
        is_running = False
        pid = progress.get('pid')
        
        if pid:
            try:
                # 检查进程是否存在且是Python进程
                proc = psutil.Process(pid)
                # 验证进程是否还在运行
                if proc.is_running():
                    cmd = proc.cmdline()
                    # 确保这是batch_screen_trend_stocks.py进程
                    is_running = any('batch_screen_trend_stocks.py' in item for item in cmd)
                    logger.debug(f"PID {pid} 进程运行中: {is_running}")
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                is_running = False
                logger.debug(f"PID {pid} 进程不存在或无法访问")
        
        # 检查进度文件状态
        status = progress.get('status', 'unknown')
        
        # 如果状态是error，直接返回错误
        if status == 'error':
            return {
                "success": True,
                "running": False,
                "processed": progress.get('processed_count', 0),
                "total": progress.get('total_count', 0),
                "trade_date": progress.get('trade_date', ''),
                "timestamp": progress.get('timestamp', ''),
                "error": "获取股票列表失败（网络原因），请稍后重试"
            }
        
        # 如果进程不存在，检查是否刚刚完成（最近10秒内）
        if not is_running:
            last_update = progress.get('timestamp', '')
            if last_update:
                try:
                    last_update_dt = datetime.strptime(last_update, "%Y-%m-%d %H:%M:%S")
                    elapsed = (datetime.now() - last_update_dt).total_seconds()
                    # 如果进程不存在但最近10秒更新过，暂时认为在运行（可能刚结束）
                    if elapsed < 10 and progress.get('processed_count', 0) < progress.get('total_count', 0):
                        is_running = True
                except ValueError:
                    pass
        
        return {
            "success": True,
            "running": is_running,
            "processed": progress.get('processed_count', 0),
            "total": progress.get('total_count', 0),
            "trade_date": progress.get('trade_date', ''),
            "timestamp": progress.get('timestamp', '')
        }
    except Exception as e:
        logger.error(f"查询筛选进度失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e)
        }


@app.get("/api/sector-mapping/status")
async def get_sector_mapping_status():
    """检查板块映射初始化状态"""
    try:
        import sqlite3
        import os
        import json
        
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'fupan.db')
        progress_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'sector_mapping_progress.json')
        
        # 检查进度文件
        progress = None
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    progress = json.load(f)
            except:
                pass
        
        # 检查是否有进程正在运行
        is_running = False
        if progress and progress.get('status') in ('running', 'starting'):
            import time
            last_updated = progress.get('timestamp', '')
            if last_updated:
                try:
                    from datetime import datetime
                    last_update_dt = datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")
                    elapsed = (datetime.now() - last_update_dt).total_seconds()
                    # 如果5分钟内有更新，认为在运行
                    if elapsed < 300:
                        is_running = True
                except:
                    pass
        
        if not os.path.exists(db_path):
            return {
                "success": True,
                "has_mapping": False,
                "stock_count": 0,
                "running": is_running,
                "progress": progress,
                "message": "数据库不存在"
            }
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM stocks WHERE all_sectors IS NOT NULL")
        count = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "success": True,
            "has_mapping": count > 0,
            "stock_count": count,
            "running": is_running,
            "progress": progress,
            "message": "板块映射已初始化" if count > 0 else "板块映射未初始化"
        }
    except Exception as e:
        logger.error(f"检查板块映射状态失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e)
        }


@app.post("/api/sector-mapping/init")
async def init_sector_mapping():
    """初始化板块映射（启动init_stock_sector_mapping.py脚本）"""
    try:
        import subprocess
        import os
        import sys
        import json
        
        # 检查是否有任务在运行
        progress_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'sector_mapping_progress.json')
        
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    progress = json.load(f)
                
                if progress.get('status') in ('running', 'starting'):
                    last_timestamp = progress.get('timestamp', '')
                    if last_timestamp:
                        from datetime import datetime
                        last_update = datetime.strptime(last_timestamp, "%Y-%m-%d %H:%M:%S")
                        elapsed = (datetime.now() - last_update).total_seconds()
                        if elapsed < 600:  # 10分钟内有更新
                            return {
                                "success": False,
                                "message": "板块映射初始化任务正在运行中"
                            }
            except:
                pass
        
        # 清理旧的进度文件
        if os.path.exists(progress_file):
            try:
                os.remove(progress_file)
            except:
                pass
        
        # 启动初始化脚本
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'init_stock_sector_mapping.py')
        
        if not os.path.exists(script_path):
            return {
                "success": False,
                "message": f"初始化脚本不存在: {script_path}"
            }
        
        # 使用subprocess.Popen异步运行
        # 不捕获stdout/stderr，让子进程直接输出到控制台
        process = subprocess.Popen(
            [sys.executable, script_path],
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        logger.info(f"已启动板块映射初始化，进程ID: {process.pid}")
        
        return {
            "success": True,
            "message": f"板块映射初始化已启动，进程ID: {process.pid}",
            "pid": process.pid
        }
    except Exception as e:
        logger.error(f"启动板块映射初始化失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e)
        }


@app.post("/api/sector-scoring/run")
async def run_sector_scoring():
    """运行板块评分流程（分析→查询→更新）"""
    try:
        import subprocess
        import os
        import sys
        
        # 按顺序执行三个脚本的shell脚本
        commands = [
            f'cd "{os.path.dirname(os.path.dirname(__file__))}"',
            f'"{sys.executable}" analyze_trend_stocks_by_sector.py',
            f'"{sys.executable}" fetch_sector_strength.py',
            f'"{sys.executable}" update_trend_stocks_with_sector_score.py'
        ]
        
        # 创建临时批处理脚本（Windows）
        batch_script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'run_sector_scoring.bat')
        log_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs', 'sector_scoring.log')
        
        with open(batch_script_path, 'w', encoding='utf-8') as f:
            f.write('chcp 65001\n')
            f.write('\n'.join(commands))
            f.write('\n')
        
        # 启动批处理脚本
        process = subprocess.Popen(
            batch_script_path,
            stdout=open(log_file, 'w', encoding='utf-8') if log_file else subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        
        logger.info(f"已启动板块评分流程，进程ID: {process.pid}")
        
        return {
            "success": True,
            "message": f"板块评分流程已启动，进程ID: {process.pid}",
            "pid": process.pid,
            "log_file": log_file
        }
    except Exception as e:
        logger.error(f"启动板块评分流程失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e)
        }

    except Exception as e:
        logger.error(f"获取股票K线失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "klines": []
        }


@app.get("/api/mairui-api-usage")
async def get_mairui_api_usage():
    """获取Mairui API调用统计"""
    try:
        from src.data_acquisition import get_mairui_api_stats
        return get_mairui_api_stats()
    except Exception as e:
        logger.error(f"获取Mairui API调用统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ========== 盘中记录 API ==========
# 独立模块：src/intraday_notes.py
# 数据库表：intraday_notes
# 日期跟随统一日期选择器（与涨跌停统计/连板梯队/市场总结一致）

from src.intraday_notes import (
    list_notes as _list_notes,
    create_note as _create_note,
    update_note as _update_note,
    delete_note as _delete_note,
    get_time_rules as _get_time_rules,
)


@app.get("/api/intraday-notes")
async def get_intraday_notes(date: str):
    """获取某日的所有盘中记录（按 note_time 排序）"""
    try:
        notes = _list_notes(date)
        return {"success": True, "date": date, "notes": notes, "count": len(notes)}
    except Exception as e:
        logger.error(f"获取盘中记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class IntradayNoteCreateRequest(BaseModel):
    trade_date: str
    content: str
    note_time: Optional[str] = None
    prev_time: Optional[str] = None
    next_time: Optional[str] = None


@app.post("/api/intraday-notes")
async def create_intraday_note(req: IntradayNoteCreateRequest):
    """创建或合并盘中记录（后端自动判断合并）

    - 不传 prev_time/next_time：末尾追加，无时间顺序约束（适合事后补录）
    - 传 prev_time/next_time：中间插入，时间必须严格在 [prev_time, next_time] 范围内
    """
    try:
        result = _create_note(
            req.trade_date,
            req.content,
            req.note_time,
            req.prev_time,
            req.next_time,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("msg", "创建失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建盘中记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class IntradayNoteUpdateRequest(BaseModel):
    content: Optional[str] = None
    note_time: Optional[str] = None


@app.put("/api/intraday-notes/{note_id}")
async def update_intraday_note(note_id: int, req: IntradayNoteUpdateRequest):
    """修改盘中记录（内容或时间）"""
    try:
        result = _update_note(note_id, req.content, req.note_time)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("msg", "修改失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"修改盘中记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/intraday-notes/{note_id}")
async def delete_intraday_note(note_id: int):
    """删除盘中记录"""
    try:
        result = _delete_note(note_id)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("msg", "删除失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除盘中记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/intraday-notes/time-rules")
async def get_intraday_time_rules(date: str,
                                  prev_time: Optional[str] = None,
                                  next_time: Optional[str] = None):
    """获取时间规则（前端判断用）

    - can_use_system: 当前是否可以用系统时间
    - current_time: 当前系统时间（HH:MM）
    - range: 手动选时间的可选范围
    - is_trading_date: 是否交易日
    - in_trading_hours: 是否在交易时段
    """
    try:
        rules = _get_time_rules(date, prev_time, next_time)
        return {"success": True, **rules}
    except Exception as e:
        logger.error(f"获取盘中时间规则失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
