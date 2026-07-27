"""
数据获取层 - 独立于业务逻辑
负责从API获取数据并写入数据库
"""
import logging
import json
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, TYPE_CHECKING, Any
from data.database import DB_PATH
import sqlite3
import os

# 网络/外部库改为可选依赖：缺失时仅记录警告，不阻止 server 启动
# TYPE_CHECKING 块让 IDE/LSP 知道这些是 Module 类型（运行时为 None | Module）
if TYPE_CHECKING:
    import akshare as ak  # type: ignore[import-not-found]
    import pandas as pd  # type: ignore[import-not-found]
    import requests  # type: ignore[import-not-found]

try:
    import akshare as ak  # type: ignore[assignment]
except ImportError:
    ak = None  # type: ignore[assignment]
    logging.getLogger(__name__).warning("akshare 未安装, 在线行情/涨跌停抓取功能不可用")

try:
    import pandas as pd  # type: ignore[assignment]
except ImportError:
    pd = None  # type: ignore[assignment]
    logging.getLogger(__name__).warning("pandas 未安装, 数据处理受限 (仅影响在线抓取)")

try:
    import requests  # type: ignore[assignment]
except ImportError:
    requests = None  # type: ignore[assignment]
    logging.getLogger(__name__).warning("requests 未安装, Mairui 抓取功能不可用")

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv('.env.1')
except ImportError:
    _load_dotenv = None
    logging.getLogger(__name__).warning("python-dotenv 未安装, 将从环境变量读取 Mairui 配置")

logger = logging.getLogger(__name__)

# 加载 Mairui API 配置（全局，避免重复加载）
# dotenv 缺失时直接从环境变量读取（适合手动 export / 已配置好的机器）
MAIRUI_LICENCE = os.getenv('MAIRUI_LICENCE')
MAIRUI_BASE_URL = os.getenv('MAIRUI_BASE_URL', 'https://api.mairuiapi.com')
MAIRUI_STRONG_API_URL = os.getenv('MAIRUI_STRONG_API_URL', 'hslt/qsgc')

# Mairui API 调用统计
_mairui_api_stats = {
    'used': 0,
    'limit': 50,
    'date': datetime.now().date()
}

def get_mairui_api_stats():
    """获取 Mairui API 调用统计"""
    today = datetime.now().date()
    if _mairui_api_stats['date'] != today:
        _mairui_api_stats['used'] = 0
        _mairui_api_stats['date'] = today
    return {
        'used': _mairui_api_stats['used'],
        'limit': _mairui_api_stats['limit'],
        'date': _mairui_api_stats['date'].isoformat()
    }

def increment_mairui_api_usage(count: int = 1):
    """增加 Mairui API 调用次数"""
    today = datetime.now().date()
    if _mairui_api_stats['date'] != today:
        _mairui_api_stats['used'] = 0
        _mairui_api_stats['date'] = today
    _mairui_api_stats['used'] += count


def mairui_get_with_retry(url: str, max_retries: int = 3, timeout: int = 30, retry_delay: float = 2.0):
    """Mairui API GET 请求带重试 (应对 404/502 不稳定)

    仅在状态码 200 时返回 Response 对象；其他情况 (异常/非200) 触发重试。
    重试 max_retries 次后仍失败则返回最后一次 Response 对象 (可能仍非 200)。
    """
    if requests is None:
        logger.warning("requests 未安装, Mairui 抓取不可用")
        return None
    last_response = None
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                if attempt > 1:
                    logger.info(f"  ✓ Mairui 重试 {attempt}/{max_retries} 成功")
                return r
            else:
                logger.warning(f"  Mairui 请求尝试 {attempt}/{max_retries} 失败: HTTP {r.status_code} - {r.text[:80]}")
                last_response = r
        except Exception as e:
            logger.warning(f"  Mairui 请求尝试 {attempt}/{max_retries} 异常: {type(e).__name__}: {str(e)[:80]}")
            last_error = e
        if attempt < max_retries:
            time.sleep(retry_delay)
    if last_response is not None:
        return last_response
    if last_error is not None:
        raise last_error
    return None


class DataAcquisitionService:
    """数据获取服务 - 独立于业务代码层"""

    def __init__(self):
        self.db_path = DB_PATH

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        return sqlite3.connect(self.db_path)

    async def fetch_spot_data(self) -> List[Dict]:
        """获取全市场实时行情数据

        Returns:
            包含 code, name, change_percent 等字段的字典列表
        """
        try:
            import akshare as ak
            import pandas as pd

            df = ak.stock_zh_a_spot_em()
            if df is None or len(df) == 0:
                logger.warning("未获取到实时行情数据")
                return []

            df['涨跌幅'] = pd.to_numeric(df['涨跌幅'], errors='coerce').fillna(0).astype(float)

            results = []
            for _, row in df.iterrows():
                results.append({
                    'code': str(row.get('代码', '')),
                    'name': str(row.get('名称', '')),
                    'change_percent': float(row.get('涨跌幅', 0)),
                    'price': float(row.get('最新价', 0)),
                    'amount': float(row.get('成交额', 0)),
                })
            logger.info(f"获取实时行情数据: {len(results)} 条")
            return results
        except Exception as e:
            logger.error(f"获取实时行情数据失败: {e}", exc_info=True)
            return []

    def _get_or_create_stock(self, stock_code: str, stock_name: str, industry: str = '', conn=None, cursor=None) -> int:
        """获取或创建股票记录
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            industry: 行业
            conn: 外部传入的数据库连接（可选）
            cursor: 外部传入的游标（可选）
            
        Returns:
            stock_id
        """
        external_conn = conn is not None and cursor is not None
        should_close = not external_conn
        
        if not external_conn:
            conn = self._get_connection()
            cursor = conn.cursor()
        
        try:
            cursor.execute('SELECT stock_id FROM stocks WHERE stock_code = ?', (stock_code,))
            result = cursor.fetchone()

            if result:
                stock_id = result[0]
                # 只在industry为空时才更新，避免覆盖已有数据
                cursor.execute('''
                    UPDATE stocks
                    SET stock_name = ?,
                        industry = CASE WHEN industry IS NULL OR industry = '' THEN ? ELSE industry END,
                        updated_at = ?
                    WHERE stock_id = ?
                ''', (stock_name, industry, datetime.now().isoformat(), stock_id))
            else:
                cursor.execute('''
                    INSERT INTO stocks (stock_code, stock_name, industry, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (stock_code, stock_name, industry, datetime.now().isoformat(), datetime.now().isoformat()))
                stock_id = cursor.lastrowid

            if not external_conn:
                conn.commit()

            return stock_id
        except Exception as e:
            print(f"❌ 获取或创建股票失败: code={stock_code}, name={stock_name}, 错误: {e}")
            return -1
        finally:
            if should_close and conn:
                conn.close()

    def fetch_and_save_limit_data(self, date: str, use_tmp_table: bool = False) -> Dict:
        """获取并保存涨停数据（包含首板、连板、统计数据）

        数据源配置：
        - 主数据源: ak.stock_zt_pool_previous_em (上一交易日)
        - 备用数据源: ak.stock_zt_pool_em(date) (当日，需交易日)

        第79行开始的代码被替换，使用了新的数据源策略。
        它优先尝试获取上一交易日的涨停池数据，如果失败，则回退到当日数据接口，
        保证了数据获取的可用性。通过这种设计，系统可以在非交易日或接口异常时，
        依然能够获取最新的涨跌停相关数据。

        Args:
            date: 交易日期，格式：YYYY-MM-DD
            use_tmp_table: 是否使用临时表（True=盘中刷新，False=盘后刷新）
                             - 盘中刷新时使用临时表
                             - 盘后刷新时使用正式表

        Returns:
            {
                "success": True/False,
                "message": "执行结果描述",
                "first_limit_count": 首板数量,
                "continuous_limit_count": 连板数量,
                "exploded_count": 炸板数量,
                "limit_down_count": 跌停数量,
                "total_records": 总记录数
            }
        """
        try:
            results = {
                "success": False,
                "message": "",
                "first_limit_count": 0,
                "continuous_limit_count": 0,
                "exploded_count": 0,
                "limit_down_count": 0,
                "total_records": 0,
                # 当日涨停池全量数据（code + change_pct），供溢价快照等下游使用
                "limit_pool": []
            }

            # 根据use_tmp_table参数确定使用的表
            first_limits_table = "first_limits_tmp" if use_tmp_table else "first_limits"
            first_limit_topics_table = "first_limit_topics_tmp" if use_tmp_table else "first_limit_topics"
            if use_tmp_table:
                continuous_limits_table = None
            else:
                continuous_limits_table = "continuous_limits_history"
            limit_down_table = "limit_down_tmp" if use_tmp_table else "limit_down"
            exploded_table = "exploded_tmp" if use_tmp_table else "exploded"
            limit_stats_table = "limit_stats_tmp" if use_tmp_table else "limit_stats"

            logger.info(f"开始获取 {date} 的涨停数据")
            logger.info(f"使用临时表: {use_tmp_table}")

            # 1. 获取涨停板数据（直接使用当日涨停池）
            df_limit = None
            last_error = None

            # 使用当日涨停池API：stock_zt_pool_em
            try:
                logger.info(f"获取当日涨停数据: stock_zt_pool_em, 日期: {date}")

                date_for_akshare = date.replace('-', '')
                df_limit = ak.stock_zt_pool_em(date=date_for_akshare)

                if df_limit is not None and len(df_limit) > 0:
                    logger.info(f"✓ 获取涨停板数据成功: {len(df_limit)} 条")
                    logger.info(f"  DataFrame列名: {list(df_limit.columns)}")
                    logger.info(f"  前5行数据预览:\n{df_limit.head().to_string()}")
                    results["total_records"] += len(df_limit)
                    # 收集所有涨停标的 code + 涨跌幅，供溢价快照等下游使用
                    for _, _row in df_limit.iterrows():
                        _code = str(_row.get('代码', ''))
                        _change = float(_row.get('涨跌幅', 0)) if pd.notna(_row.get('涨跌幅')) else None
                        if _code:
                            results["limit_pool"].append({"code": _code, "change_percent": _change})
                else:
                    logger.warning(f"⚠️ 返回空数据: {date}")
                    logger.warning(f"  可能原因：")
                    logger.warning(f"    1. 今天是非交易日")
                    logger.warning(f"    2. 该日期的涨停数据尚未更新")
                    logger.warning(f"    3. 数据源暂不可用")
                    last_error = "返回空数据（可能是非交易日或数据未更新）"

            except Exception as e:
                logger.warning(f"获取涨停板数据失败: stock_zt_pool_em, 错误: {e}")
                last_error = f"接口调用失败: {str(e)}"
                logger.warning(f"错误详情: {type(e).__name__}: {str(e)}")

            # 2. 获取跌停板数据（使用 Mairui API）
            # 盘中刷新时跳过跌停数据获取，节省API额度
            if use_tmp_table:
                logger.info("盘中模式：跳过跌停数据获取")
                results["limit_down_count"] = 0
            else:
                mairui_limit_down_count = 0
                limit_down_data = []

                if MAIRUI_LICENCE:
                    try:
                        dt_url = f"{MAIRUI_BASE_URL}/hslt/dtgc/{date}/{MAIRUI_LICENCE}"
                        logger.info(f"调用 Mairui 跌停接口: {dt_url}")
                        dt_response = mairui_get_with_retry(dt_url)

                        if dt_response and dt_response.status_code == 200:
                            increment_mairui_api_usage()
                            limit_down_data = dt_response.json() if isinstance(dt_response.json(), list) else []
                            mairui_limit_down_count = len(limit_down_data)
                            logger.info(f"✓ Mairui 跌停数据获取成功: {mairui_limit_down_count} 只")
                            if limit_down_data:
                                logger.info(f"[DEBUG] Mairui 跌停数据字段名: {list(limit_down_data[0].keys()) if limit_down_data else '无数据'}")
                                logger.info(f"[DEBUG] Mairui 跌停数据样本（前2条）: {json.dumps(limit_down_data[:2], ensure_ascii=False)[:500]}")
                        else:
                            logger.warning(f"Mairui 跌停接口最终失败: {dt_response.status_code if dt_response else 'N/A'}")
                    except Exception as e:
                        logger.warning(f"获取 Mairui 跌停数据失败: {e}")
                else:
                    logger.warning("Mairui License 未配置")

                results["limit_down_count"] = mairui_limit_down_count
                if mairui_limit_down_count == 0:
                    logger.info("Mairui 跌停数据未获取到，设置为0")
                else:
                    # 保存跌停数据到 limit_down 表
                    try:
                        saved_count = self._save_limit_down_data(limit_down_data, date)
                        logger.info(f"跌停数据已保存: {saved_count} 条")
                    except Exception as e:
                        logger.warning(f"保存跌停数据失败: {e}")


            # 3. 处理涨停板数据并保存到数据库
            first_limit_stocks = []
            continuous_limit_stocks = []
            all_limit_stocks = []

            # 先收集所有数据，然后批量保存
            to_save_first_limits = []
            to_save_continuous_limits = []

            if df_limit is None or len(df_limit) == 0:
                logger.warning(f"DataFrame为空，无法处理数据")
                return {
                    "success": False,
                    "message": f"数据源返回空数据: {last_error}",
                    "first_limit_count": 0,
                    "continuous_limit_count": 0,
                    "exploded_count": 0,
                    "limit_down_count": 0,
                    "total_records": 0
                }

            for _, row in df_limit.iterrows():
                try:
                    # 解析数据字段（根据AkShare返回的列名）
                    code = str(row.get('代码', ''))
                    name = str(row.get('名称', ''))
                    price = float(row.get('最新价', 0)) if pd.notna(row.get('最新价')) else 0
                    change_pct = float(row.get('涨跌幅', 0)) if pd.notna(row.get('涨跌幅')) else 0

                    # 封板资金（字段名可能不同，尝试多个可能的列名）
                    amount = 0
                    for col in ['封板资金', '封单', '金额', '成交额']:
                        if col in row.index and pd.notna(row[col]):
                            amount = float(row[col])
                            break

                    # 首次封板时间（旧接口）或 昨日封板时间（新接口）
                    first_time = str(row.get('首次封板时间', ''))
                    if not first_time:
                        first_time = str(row.get('昨日封板时间', ''))
                    # 格式化时间：134839 -> 13:48:39
                    if first_time and len(first_time) == 6 and first_time.isdigit():
                        first_time = f"{first_time[:2]}:{first_time[2:4]}:{first_time[4:6]}"

                    # 炸板次数（新接口中这个字段不存在，需要从涨停统计中解析）
                    exploded_count = 0
                    for col in ['炸板次数', '炸板']:
                        if col in row.index and pd.notna(row[col]):
                            exploded_count = int(row[col])
                            break

                    # 连板数（关键字段）
                    # 新接口：昨日连板数表示昨天涨停板数，实际连板数 = 昨日连板数 + 1
                    # 旧接口：连板数直接表示当前连板数
                    continuous_days = 0
                    if '连板数' in row.index and pd.notna(row['连板数']):
                        continuous_days = int(row['连板数'])
                    elif '连板' in row.index and pd.notna(row['连板']):
                        continuous_days = int(row['连板'])
                    elif '连续涨停天数' in row.index and pd.notna(row['连续涨停天数']):
                        continuous_days = int(row['连续涨停天数'])
                    elif '昨日连板数' in row.index and pd.notna(row['昨日连板数']):
                        # 新接口：昨日连板数 + 1 = 实际连板数
                        continuous_days = int(row['昨日连板数']) + 1

                    if continuous_days == 0:
                        logger.warning(f"⚠️ 股票 {code} {name} 的连板数无法确定，已设置为0")

                    # 所属行业
                    sector = str(row.get('所属行业', ''))

                    # 跳过无效数据
                    if not code or not name or price <= 0:
                        continue

                    all_limit_stocks.append({
                        'code': code,
                        'name': name,
                        'price': price,
                        'change_pct': change_pct,
                        'amount': amount,
                        'first_time': first_time,
                        'continuous_days': continuous_days,
                        'sector': sector,
                        'exploded_count': exploded_count
                    })

                except Exception as e:
                    logger.warning(f"处理涨停记录失败: code={row.get('代码', '')}, 错误: {e}")
                    continue

            logger.info(f"DataFrame处理完成，all_limit_stocks共 {len(all_limit_stocks)} 条")

            # 打印前10条数据用于调试
            if len(all_limit_stocks) > 0:
                logger.info(f"数据样本（前10条）:")
                for i, stock in enumerate(all_limit_stocks[:10], 1):
                    logger.info(f"  {i}. {stock['code']} {stock['name']} - {stock['continuous_days']}板 - 首封:{stock['first_time']} - 炸板:{stock['exploded_count']}次")

            # 统计连板数分布
            days_counter = {}
            for stock in all_limit_stocks:
                days = stock['continuous_days']
                days_counter[days] = days_counter.get(days, 0) + 1

            logger.info(f"连板数分布统计:")
            for days in sorted(days_counter.keys()):
                logger.info(f"  {days}板: {days_counter[days]}只")

            total_1board = days_counter.get(1, 0)
            total_continuous = sum(count for days, count in days_counter.items() if days >= 2)
            logger.info(f"  首板(1板): {total_1board}只, 连板(2板及以上): {total_continuous}只")

            # 批量获取或创建股票记录（避免频繁开启连接）
            stock_cache = {}
            for stock_data in all_limit_stocks:
                if stock_data['code'] not in stock_cache:
                    stock_cache[stock_data['code']] = self._get_or_create_stock(stock_data['code'], stock_data['name'], stock_data['sector'])

            # 先查询已存在的首板记录（用于比较判断是否需要更新或补全）
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(f'''
                SELECT fl.id, fl.stock_id, fl.limit_date, fl.first_limit_time, fl.limit_price,
                       fl.open_price, fl.amount, fl.reason, fl.limit_type, fl.is_exploded,
                       s.stock_code
                FROM {first_limits_table} fl
                JOIN stocks s ON fl.stock_id = s.stock_id
                WHERE fl.limit_date = ?
            ''', (date,))
            
            existing_records = {}
            for row in cursor.fetchall():
                existing_records[row[1]] = {
                    'id': row[0],
                    'first_limit_time': row[3],
                    'limit_price': row[4],
                    'open_price': row[5],
                    'amount': row[6],
                    'reason': row[7],
                    'limit_type': row[8],
                    'is_exploded': bool(row[9])
                }
            
            logger.info(f"已存在的首板记录: {len(existing_records)}条")

            # 批量保存数据
            skipped_stocks = []
            updated_count = 0
            inserted_count = 0
            
            for stock_data in all_limit_stocks:
                stock_id = stock_cache.get(stock_data['code'])
                continuous_days = stock_data['continuous_days']

                if stock_id < 0:
                    skipped_stocks.append(stock_data['code'])
                    continue

                # 保存到首板表（只保存首板，连板数为1的）
                if continuous_days == 1:
                    # 检查是否已存在
                    existing = existing_records.get(stock_id)
                    
                    if existing:
                        # 检查数据是否完整，不完整的字段需要补全
                        needs_patch = False
                        update_fields = []
                        new_data = {
                            'first_limit_time': stock_data['first_time'],
                            'limit_price': stock_data['price'],
                            'limit_type': '10%',  # 默认10%
                            'is_exploded': stock_data['exploded_count'] > 0
                        }
                        
                        # 检查并补全每个字段
                        if existing['first_limit_time'] in ['', None]:
                            update_fields.append('first_limit_time = ?')
                        if existing['limit_price'] is None or existing['limit_price'] == 0:
                            update_fields.append('limit_price = ?')
                        if existing['open_price'] is None or existing['open_price'] == 0:
                            new_data['open_price'] = stock_data['price']
                            update_fields.append('open_price = ?')
                        if existing['amount'] is None or existing['amount'] == 0:
                            update_fields.append('amount = ?')
                        if existing['reason'] in ['', None]:
                            update_fields.append('reason = ?')
                        
                        # 检查是否需要更新（数据有变化）
                        if (abs(existing['limit_price'] - stock_data['price']) > 0.01 or
                            abs((existing['amount'] or 0) - stock_data['amount']) > 1000000 or
                            existing['is_exploded'] != (stock_data['exploded_count'] > 0)):
                            needs_patch = True
                        
                        if needs_patch or len(update_fields) > 0:
                            # 执行更新或补全
                            if len(update_fields) > 0:
                                update_sql = f"UPDATE {first_limits_table} SET {', '.join(update_fields)}, create_time = ? WHERE id = ?"
                                update_values = []
                                for field in update_fields:
                                    key = field.split(' = ')[0].strip()
                                    if key == 'first_limit_time':
                                        update_values.append(new_data['first_limit_time'])
                                    elif key == 'limit_price':
                                        update_values.append(new_data['limit_price'])
                                    elif key == 'open_price':
                                        update_values.append(new_data['open_price'])
                                    elif key == 'amount':
                                        update_values.append(stock_data['amount'])
                                    elif key == 'reason':
                                        update_values.append('新首板涨停')
                                    elif key == 'is_exploded':
                                        update_values.append(new_data['is_exploded'])
                                    elif key == 'limit_type':
                                        update_values.append('10%')
                                update_values.append(datetime.now().isoformat())
                                update_values.append(existing['id'])
                                
                                cursor.execute(update_sql, update_values)
                            else:
                                # 只更新时间
                                cursor.execute(f'UPDATE {first_limits_table} SET create_time = ? WHERE id = ?',
                                               (datetime.now().isoformat(), existing['id']))

                            updated_count += 1
                            # 根据真实涨跌幅判断涨停类型
                            limit_type = '10%'
                            change_pct = stock_data.get('change_pct', 0)
                            if change_pct >= 19.5 and change_pct <= 20.5:
                                limit_type = '20%'
                            elif change_pct >= 29.5 and change_pct <= 30.5:
                                limit_type = '30%'

                            to_save_first_limits.append((
                                stock_id, date, stock_data['first_time'], stock_data['price'],
                                stock_data['amount'], f'首板涨停', stock_data['exploded_count'] > 0, datetime.now().isoformat(), limit_type
                            ))
                        else:
                            # 数据完整且未变化，跳过
                            continue
                    else:
                        # 新记录，插入
                        # 根据真实涨跌幅判断涨停类型
                        limit_type = '10%'
                        change_pct = stock_data.get('change_pct', 0)
                        if change_pct >= 19.5 and change_pct <= 20.5:
                            limit_type = '20%'
                        elif change_pct >= 29.5 and change_pct <= 30.5:
                            limit_type = '30%'

                        to_save_first_limits.append((
                            stock_id, date, stock_data['first_time'], stock_data['price'],
                            stock_data['amount'], f'首板涨停', stock_data['exploded_count'] > 0, datetime.now().isoformat(), limit_type
                        ))
                        inserted_count += 1
                    
                    first_limit_stocks.append(stock_data['code'])

                # 保存到连板梯队历史表（包括首板和连板）
                if not use_tmp_table and continuous_days >= 1:
                    to_save_continuous_limits.append((
                        date, stock_data['code'], stock_data['name'], stock_data['price'],
                        stock_data['first_time'], continuous_days, stock_data['sector'],
                        '首板' if continuous_days == 1 else f'{continuous_days}连板',
                        stock_data['amount'], datetime.now().isoformat()
                    ))
                    continuous_limit_stocks.append(stock_data['code'])

            # 批量插入数据
            if to_save_first_limits:
                # 在 INSERT OR REPLACE 之前，记录旧的 first_limit_id
                stock_date_pairs = [(item[0], item[1]) for item in to_save_first_limits]
                old_first_limit_ids = {}
                for stock_id, limit_date in stock_date_pairs:
                    cursor.execute(f'''
                        SELECT id FROM {first_limits_table}
                        WHERE stock_id = ? AND limit_date = ?
                    ''', (stock_id, limit_date))
                    result = cursor.fetchone()
                    if result:
                        old_first_limit_ids[(stock_id, limit_date)] = result[0]

                # 执行 INSERT OR REPLACE（会删除旧记录并创建新ID）
                cursor.executemany(f'''
                    INSERT OR REPLACE INTO {first_limits_table}
                    (stock_id, limit_date, first_limit_time, limit_price, amount, reason, is_exploded, source, create_time, limit_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'akshare', ?, ?)
                ''', to_save_first_limits)

                # 更新 first_limit_topics 表中的 first_limit_id 为新的ID
                updated_count = 0
                for (stock_id, limit_date), old_id in old_first_limit_ids.items():
                    cursor.execute(f'''
                        SELECT id FROM {first_limits_table}
                        WHERE stock_id = ? AND limit_date = ?
                    ''', (stock_id, limit_date))
                    result = cursor.fetchone()
                    if result and result[0] != old_id:
                        new_id = result[0]
                        cursor.execute(f'''
                            UPDATE {first_limit_topics_table}
                            SET first_limit_id = ?
                            WHERE first_limit_id = ? AND association_date = ?
                        ''', (new_id, old_id, limit_date))
                        updated_count += cursor.rowcount

                if updated_count > 0:
                    logger.info(f"✓ 数据刷新后更新 first_limit_topics 表的 first_limit_id: {updated_count}条")

            if to_save_continuous_limits and continuous_limits_table:
                cursor.executemany(f'''
                    INSERT OR REPLACE INTO {continuous_limits_table}
                    (trade_date, code, name, price, first_time, continuous_days, sector, reason, amount, create_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', to_save_continuous_limits)

            conn.commit()
            conn.close()

            first_limit_count = len(first_limit_stocks)
            continuous_limit_count = sum(1 for s in all_limit_stocks if s['continuous_days'] >= 2)
            exploded_count = 0

            # 获取两种类型的炸板数据并存入exploded表
            # 仅在非临时表（盘后刷新）时获取炸板数据
            # 类型1：涨停股池中炸过板的票（hslt/ztgc接口，最终涨停）
            # 类型2：炸板且最终未涨停的票（hslt/zbgc接口，未涨停）- 用于limit_stats统计
            exploded_stocks_ztgc = []
            exploded_stocks_zbgc = []

            if not use_tmp_table and MAIRUI_LICENCE:
                    # 类型1：从hslt/ztgc接口获取涨停股池中炸过板的票
                    try:
                        ztgc_url = f"{MAIRUI_BASE_URL}/hslt/ztgc/{date}/{MAIRUI_LICENCE}"
                        logger.info(f"调用 Mairui 涨停接口获取炸板数据: {ztgc_url}")
                        ztgc_response = mairui_get_with_retry(ztgc_url)

                        if ztgc_response and ztgc_response.status_code == 200:
                            increment_mairui_api_usage()
                            ztgc_data = ztgc_response.json() if isinstance(ztgc_response.json(), list) else []

                            logger.info(f"[DEBUG] Mairui 涨停数据获取成功: {len(ztgc_data)} 条")
                            if ztgc_data:
                                logger.info(f"[DEBUG] Mairui 涨停数据字段名: {list(ztgc_data[0].keys())}")

                            # 筛选zbc>0的股票（炸过板但最终涨停的票）
                            exploded_stocks_ztgc = [
                                {
                                    'code': stock.get('dm', ''),
                                    'name': stock.get('mc', ''),
                                    'price': stock.get('p', 0),
                                    'first_time': stock.get('lbt', ''),
                                    'zbc': stock.get('zbc', 0),
                                    'lbc': stock.get('lbc', 0),
                                    'amount': stock.get('cje', 0),
                                    'sector': stock.get('hy', ''),
                                    'explode_type': 'limit_with_explode'
                                }
                                for stock in ztgc_data
                                if stock.get('zbc', 0) > 0
                            ]
                            logger.info(f"✓ 涨停股池中炸过的票: {len(exploded_stocks_ztgc)} 只")
                            if exploded_stocks_ztgc:
                                logger.info(f"[DEBUG] 涨停炸板股票样本（前2条）: {json.dumps(exploded_stocks_ztgc[:2], ensure_ascii=False)[:500]}")
                        else:
                            logger.warning(f"Mairui 涨停接口最终失败: {ztgc_response.status_code if ztgc_response else 'N/A'}")
                    except Exception as e:
                        logger.warning(f"获取 Mairui 涨停数据失败: {e}")

                    # 类型2：从hslt/zbgc接口获取炸板且最终未涨停的票（用于limit_stats统计）
                    try:
                        zbgc_url = f"{MAIRUI_BASE_URL}/hslt/zbgc/{date}/{MAIRUI_LICENCE}"
                        logger.info(f"调用 Mairui 炸板接口获取炸板数据: {zbgc_url}")
                        zbgc_response = mairui_get_with_retry(zbgc_url)

                        if zbgc_response and zbgc_response.status_code == 200:
                            increment_mairui_api_usage()
                            zbgc_data = zbgc_response.json() if isinstance(zbgc_response.json(), list) else []

                            logger.info(f"[DEBUG] Mairui 炸板数据获取成功: {len(zbgc_data)} 条")
                            if zbgc_data:
                                logger.info(f"[DEBUG] Mairui 炸板数据字段名: {list(zbgc_data[0].keys())}")
                                logger.info(f"[DEBUG] Mairui 炸板数据样本（第1条）: {json.dumps(zbgc_data[0], ensure_ascii=False)[:500]}")

                            # 提取炸板且最终未涨停的股票数据
                            exploded_stocks_zbgc = [
                                {
                                    'code': stock.get('dm', ''),
                                    'name': stock.get('mc', ''),
                                    'price': stock.get('p', 0),
                                    'first_time': stock.get('lbt', ''),
                                    'zbc': stock.get('zbc', 0),
                                    'lbc': stock.get('lbc', 0),
                                    'amount': stock.get('cje', 0),
                                    'sector': stock.get('hy', ''),
                                    'explode_type': 'pure_explode'
                                }
                                for stock in zbgc_data
                            ]

                            # 统计炸板数量（用于limit_stats）
                            exploded_count = len(exploded_stocks_zbgc)
                            logger.info(f"✓ Mairui 炸板数据获取成功: {exploded_count} 只（炸板且最终未涨停）")
                            if exploded_stocks_zbgc:
                                logger.info(f"[DEBUG] 炸板且未涨停股票样本（前2条）: {json.dumps(exploded_stocks_zbgc[:2], ensure_ascii=False)[:500]}")
                        else:
                            logger.warning(f"Mairui 炸板接口最终失败: {zbgc_response.status_code if zbgc_response else 'N/A'}，使用 AkShare 数据作为备用")
                    except Exception as e:
                        logger.warning(f"获取 Mairui 炸板数据失败: {e}，使用 AkShare 数据作为备用")

            total_for_rate = exploded_count + first_limit_count + continuous_limit_count
            explode_rate = (exploded_count / total_for_rate * 100) if total_for_rate > 0 else 0

            results["first_limit_count"] = first_limit_count
            results["continuous_limit_count"] = continuous_limit_count
            results["exploded_count"] = exploded_count

            # 仅在非临时表（盘后刷新）时保存统计数据
            if not use_tmp_table:
                try:
                    self.save_limit_stats(date, first_limit_count, continuous_limit_count, exploded_count, results["limit_down_count"], explode_rate, limit_stats_table)
                except Exception as e:
                    logger.error(f"保存涨跌停统计数据失败，但不影响其他数据: {e}")
                    results["limit_stats_error"] = str(e)
            else:
                logger.info("盘中模式：跳过统计数据保存")

            # 保存两种炸板数据到 exploded 表
            # 类型1：涨停股池中炸过的票
            if exploded_stocks_ztgc:
                try:
                    saved_count = self._save_exploded_data(exploded_stocks_ztgc, date, 'limit_with_explode', exploded_table)
                    logger.info(f"涨停炸板数据已保存: {saved_count} 条")
                except Exception as e:
                    logger.warning(f"保存涨停炸板数据失败: {e}")

            # 类型2：炸板且最终未涨停的票
            if exploded_stocks_zbgc:
                try:
                    saved_count = self._save_exploded_data(exploded_stocks_zbgc, date, 'pure_explode', exploded_table)
                    logger.info(f"炸板且未涨停数据已保存: {saved_count} 条")
                except Exception as e:
                    logger.warning(f"保存炸板未涨停数据失败: {e}")

            logger.info(f"涨停数据保存完成: 首板{first_limit_count}只, 连板{continuous_limit_count}只, 炸板{exploded_count}次, 跌停{results['limit_down_count']}只")
            results["success"] = True
            results["message"] = f"成功保存涨停数据: 首板{first_limit_count}只, 连板{continuous_limit_count}只"

            if "limit_stats_error" in results:
                results["message"] += f"（统计表保存失败，请检查日志）"

            return results


        except Exception as e:
            logger.error(f"获取并保存涨停数据失败: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"系统错误: {e}",
                "first_limit_count": 0,
                "continuous_limit_count": 0,
                "exploded_count": 0,
                "limit_down_count": 0,
                "total_records": 0
            }

    def save_limit_stats(self, trade_date: str, first_limit: int, continuous_limit: int,
                         exploded: int, limit_down: int, explode_rate: float,
                         limit_stats_table: str = "limit_stats", market_mood: int = 3) -> bool:
        """保存涨跌停统计数据

        Args:
            trade_date: 交易日期
            first_limit: 首板数量
            continuous_limit: 连板数量
            exploded: 炸板数量
            limit_down: 跌停数量
            explode_rate: 炸板率
            limit_stats_table: 统计表名（limit_stats 或 limit_stats_tmp）
            market_mood: 市场情绪

        Returns:
            True: 保存成功
            False: 保存失败

        Raises:
            Exception: 保存失败时抛出异常（包含详细信息）
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()

            cursor.execute(f'''
                INSERT INTO {limit_stats_table}
                (trade_date, first_limit, continuous_limit, exploded, limit_down, explode_rate, market_mood, create_time, update_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    first_limit = excluded.first_limit,
                    continuous_limit = excluded.continuous_limit,
                    exploded = excluded.exploded,
                    limit_down = excluded.limit_down,
                    explode_rate = excluded.explode_rate,
                    update_time = ?
            ''', (trade_date, first_limit, continuous_limit, exploded, limit_down, explode_rate, market_mood, now, now, now))

            conn.commit()
            conn.close()

            logger.info(f"✓ 涨跌停统计数据已保存: {trade_date}, 首板={first_limit}, 连板={continuous_limit}, 炸板={exploded}, 跌停={limit_down}, 炸板率={explode_rate:.2f}%")
            return True
        except Exception as e:
            error_msg = f"保存涨跌停统计数据失败: {trade_date}, 错误: {e}"
            logger.error(error_msg, exc_info=True)
            raise Exception(error_msg) from e

    def _save_limit_down_data(self, limit_down_data: List[Dict], trade_date: str) -> int:
        """保存跌停数据到 limit_down 表和 limit_down_history 表
        
        Args:
            limit_down_data: Mairui 返回的跌停股票列表
            trade_date: 交易日期（查询参数）
            
        Returns:
            保存的记录数
        """
        try:
            if not limit_down_data or len(limit_down_data) == 0:
                logger.info("跌停数据为空，跳过保存")
                return 0
            
            conn = self._get_connection()
            cursor = conn.cursor()
            
            saved_count = 0
            history_saved_count = 0
            
            for stock in limit_down_data:
                try:
                    code = stock.get('dm', '')
                    name = stock.get('mc', '')
                    price = float(stock.get('p', 0)) if stock.get('p') else 0
                    change_pct = float(stock.get('zf', 0)) if stock.get('zf') else 0
                    amount = float(stock.get('cje', 0)) if stock.get('cje') else 0
                    sector = stock.get('hy', '')
                    reason = stock.get('reason', '')
                    
                    if not code or not name:
                        continue
                    
                    stock_id = self._get_or_create_stock(code, name, sector, conn, cursor)
                    create_time = f"{trade_date}T16:00:00"
                    
                    # 保存到 limit_down 表（单日记录）
                    cursor.execute('''
                        INSERT OR REPLACE INTO limit_down
                        (stock_id, trade_date, price, change_percent, amount, reason, source, create_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (stock_id, trade_date, price, change_pct, amount, reason, 'mairui', create_time))
                    
                    saved_count += 1
                    
                except Exception as e:
                    logger.warning(f"保存跌停记录失败: code={stock.get('code', '')}, error={e}")
                    continue
            
            conn.commit()
            
            # 保存到 limit_down_history 表（记录连续跌停天数）
            history_saved_count = self._save_limit_down_history_data(conn, cursor, trade_date)
            
            conn.close()
            
            logger.info(f"跌停数据保存完成: {saved_count} 条")
            logger.info(f"连续跌停历史保存完成: {history_saved_count} 条")
            return saved_count
            
        except Exception as e:
            logger.error(f"保存跌停数据失败: {e}", exc_info=True)
            return 0

    def _save_limit_down_history_data(self, conn, cursor, trade_date: str) -> int:
        """保存连续跌停历史数据到 limit_down_history 表

        Args:
            conn: 数据库连接
            cursor: 数据库游标
            trade_date: 交易日期（查询参数）

        Returns:
            保存的记录数
        """
        try:
            # 获取今日跌停的所有股票
            cursor.execute('''
                SELECT s.stock_id, s.stock_code, s.stock_name, s.industry, ld.price, ld.change_percent, ld.amount, ld.reason
                FROM limit_down ld
                JOIN stocks s ON ld.stock_id = s.stock_id
                WHERE ld.trade_date = ?
            ''', (trade_date,))

            limit_down_stocks = cursor.fetchall()
            if not limit_down_stocks:
                logger.info("今日无跌停股票，跳过保存连续跌停历史")
                return 0

            saved_count = 0

            for stock_row in limit_down_stocks:
                try:
                    stock_id = stock_row[0]
                    code = stock_row[1]
                    name = stock_row[2]
                    sector = stock_row[3]
                    price = stock_row[4]
                    change_pct = stock_row[5]
                    amount = stock_row[6]
                    reason = stock_row[7]

                    # 查询该股票历史跌停日期
                    cursor.execute('''
                        SELECT trade_date
                        FROM limit_down
                        WHERE stock_id = ?
                        ORDER BY trade_date DESC
                    ''', (stock_id,))

                    down_dates = [row[0] for row in cursor.fetchall()]

                    # 计算连续跌停天数（从今天向前统计连续的日期）
                    continuous_days = 1
                    if len(down_dates) > 1:
                        # 检查是否连续
                        from datetime import datetime, timedelta
                        current_date = datetime.strptime(trade_date, '%Y-%m-%d')
                        
                        for i in range(1, len(down_dates)):
                            prev_date = datetime.strptime(down_dates[i], '%Y-%m-%d')
                            expected_date = current_date - timedelta(days=i)
                            
                            # 如果日期连续，继续计数
                            if prev_date == expected_date:
                                continuous_days += 1
                            else:
                                break
                    
                    # 如果只有1天或不到2天，不保存到历史表
                    if continuous_days < 2:
                        continue

                    # 保存到 limit_down_history 表
                    create_time = f"{trade_date}T16:00:00"
                    cursor.execute('''
                        INSERT OR REPLACE INTO limit_down_history
                        (trade_date, code, name, price, change_percent, continuous_days, sector, reason, amount, create_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (trade_date, code, name, price, change_pct, continuous_days, sector, reason, amount, create_time))

                    saved_count += 1

                except Exception as e:
                    logger.warning(f"保存连续跌停历史失败: code={code if 'code' in locals() else 'unknown'}, error={e}")
                    continue

            logger.info(f"连续跌停历史保存完成: {saved_count} 条")
            return saved_count

        except Exception as e:
            logger.error(f"保存连续跌停历史失败: {e}", exc_info=True)
            return 0

    def _save_exploded_data(self, exploded_list: List[Dict], trade_date: str, explode_type: str, exploded_table: str = "exploded") -> int:
        """保存炸板数据到 exploded 或 exploded_tmp 表

        Args:
            exploded_list: 炸板股票列表
            trade_date: 交易日期（查询参数）
            explode_type: 炸板类型（'limit_with_explode' 或 'pure_explode'）
                - 'limit_with_explode': 涨停股池中炸过板的票（hslt/ztgc接口，最终涨停）
                - 'pure_explode': 炸板且最终未涨停的票（hslt/zbgc接口，未涨停）
            exploded_table: 表名（'exploded' 或 'exploded_tmp'）

        Returns:
            保存的记录数
        """
        try:
            if not exploded_list or len(exploded_list) == 0:
                logger.info("炸板数据为空，跳过保存")
                return 0

            conn = self._get_connection()
            cursor = conn.cursor()

            saved_count = 0

            for stock in exploded_list:
                try:
                    code = stock.get('code', '')
                    name = stock.get('name', '')
                    price = float(stock.get('price', 0)) if stock.get('price') else 0
                    exploded_count = int(stock.get('zbc', 0))
                    continuous_days = int(stock.get('lbc', 0))
                    amount = float(stock.get('amount', 0)) if stock.get('amount') else 0
                    sector = stock.get('sector', '')
                    first_time = stock.get('first_time', '')

                    if not code or not name:
                        continue

                    stock_id = self._get_or_create_stock(code, name, sector, conn, cursor)
                    create_time = f"{trade_date}T16:00:00"

                    cursor.execute(f'''
                        INSERT OR REPLACE INTO {exploded_table}
                        (stock_id, trade_date, limit_price, first_limit_time, exploded_count,
                         continuous_days, amount, sector, reason, source, explode_type, create_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (stock_id, trade_date, price, first_time, exploded_count,
                           continuous_days, amount, sector, '炸板', 'mairui', explode_type, create_time))

                    saved_count += 1

                except Exception as e:
                    logger.warning(f"保存炸板记录失败: code={stock.get('code', '')}, error={e}")
                    continue

            conn.commit()
            conn.close()

            logger.info(f"炸板数据保存完成: {saved_count} 条 (类型: {explode_type})")
            return saved_count

        except Exception as e:
            logger.error(f"保存炸板数据失败: {e}", exc_info=True)
            return 0

    # 【已注释】获取并保存人气排行榜数据方法 - 包含"人气、热度"相关的新高榜单获取
    def fetch_and_save_popularity_ranking(self, source_name: str, trade_date: str = None) -> Dict:
        """
        获取并保存人气排行榜数据 - 包含"人气、热度"相关的新高榜单
        关联关系：
        - 调用方：main.py 的 refresh_hot_stocks（line 2966）调用此方法
        - 数据源：AkShare 的 stock_rank_cxg_ths API（新高榜单：半年新高、一年新高、历史新高）
        - 数据保存：调用 db_operations.save_popularity_stocks()
        - 表：popularity_sources, popularity_stocks, stocks
        过滤说明：只处理"新高榜"数据源（半年新高、一年新高、历史新高），不处理"人气榜"（热门关注、热门交易）

        Args:
            source_name: 数据源名称（半年新高、一年新高、历史新高）
            trade_date: 交易日期（格式: YYYY-MM-DD），不传则使用当前日期

        Returns:
            {
                "success": True/False,
                "message": "执行结果描述",
                "source_name": "数据源名称",
                "record_count": 保存的记录数
            }
        """
        # 【过滤】只处理"新高榜"数据源（半年新高、一年新高、历史新高）
        allowed_sources = ['半年新高', '一年新高', '历史新高']
        if source_name not in allowed_sources:
            logger.warning(f"数据源 {source_name} 不在新高榜列表中，跳过处理")
            return {
                "success": False,
                "message": f"数据源 {source_name} 不被支持（只支持新高榜：半年新高、一年新高、历史新高）",
                "source_name": source_name,
                "record_count": 0
            }

        try:
            results = {
                "success": False,
                "message": "",
                "source_name": source_name,
                "record_count": 0
            }

            logger.info(f"开始获取人气排行榜: {source_name}")

            # 【重要】新高榜单（历史新高、一年新高、半年新高）只能获取最新数据
            # 如果用户选择的日期不是最新交易日，跳过刷新，避免存储错误数据
            if source_name in ['半年新高', '一年新高', '历史新高']:
                # 获取最新交易日
                try:
                    from data.database import DB_PATH
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute('SELECT MAX(date) FROM trading_days WHERE date <= ?', ((datetime.now().strftime("%Y-%m-%d")),))
                    result = cursor.fetchone()
                    conn.close()
                    latest_trading_date = result[0] if result else None
                except Exception as e:
                    logger.warning(f"获取最新交易日失败: {e}，使用当前日期代替")
                    latest_trading_date = datetime.now().strftime("%Y-%m-%d")
                
                if trade_date and latest_trading_date and trade_date != latest_trading_date:
                    logger.info(f"用户选择的日期 {trade_date} 不是最新交易日（{latest_trading_date}），跳过{source_name}刷新（接口无日期参数）")
                    return {
                        "success": True,
                        "message": f"{source_name}接口无日期参数，仅支持刷新最新交易日数据，当前最新: {latest_trading_date}",
                        "source_name": source_name,
                        "record_count": 0
                    }
            
            # 使用指定的交易日期，不传则使用当前日期
            if trade_date:
                today = trade_date
            else:
                today = datetime.now().strftime("%Y-%m-%d")

            # 根据数据源名称选择对应的API
            df = None
            api_name = ""

            # 新的新高数据源
            if source_name == '半年新高':
                try:
                    df = ak.stock_rank_cxg_ths(symbol='半年新高')
                    api_name = "stock_rank_cxg_ths"
                except Exception as e:
                    logger.warning(f"调用 stock_rank_cxg_ths(半年新高) 失败: {e}")

            elif source_name == '一年新高':
                try:
                    df = ak.stock_rank_cxg_ths(symbol='一年新高')
                    api_name = "stock_rank_cxg_ths"
                except Exception as e:
                    logger.warning(f"调用 stock_rank_cxg_ths(一年新高) 失败: {e}")

            elif source_name == '历史新高':
                try:
                    df = ak.stock_rank_cxg_ths(symbol='历史新高')
                    api_name = "stock_rank_cxg_ths"
                except Exception as e:
                    logger.warning(f"调用 stock_rank_cxg_ths(历史新高) 失败: {e}")

            if df is None or len(df) == 0:
                logger.warning(f"未获取到 {source_name} 的数据")
                results["message"] = f"未获取到 {source_name} 的数据"
                return results

            logger.info(f"成功获取 {source_name} 数据: {len(df)} 条")

            # 获取或创建数据源记录
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('SELECT source_id FROM popularity_sources WHERE source_name = ?', (source_name,))
            result = cursor.fetchone()

            if result:
                source_id = result[0]
            else:
                # 根据数据源类型设置不同的描述
                if source_name in ['半年新高', '一年新高', '历史新高']:
                    description = f'AkShare 新高榜单 - {source_name}'
                else:
                    description = 'AkShare API'
                cursor.execute('''
                    INSERT INTO popularity_sources (source_name, description, sort_order, is_active, created_at, updated_at)
                    VALUES (?, ?, 0, 1, ?, ?)
                ''', (source_name, description, datetime.now().isoformat(), datetime.now().isoformat()))
                source_id = cursor.lastrowid
                conn.commit()

            conn.close()

            # 判断数据源类型
            is_new_high_source = source_name in ['半年新高', '一年新高', '历史新高']

            # 继续处理数据（完整的处理逻辑应该在这里）
            to_save_data = []
            stock_data_list = []

            # 新高数据全量展示
            df_filtered = df

            for _, row in df_filtered.iterrows():
                code = ''
                name = ''

                # 新高数据字段：['序号', '股票代码', '股票简称', '涨跌幅', '换手率', '最新价', '前期高点', '前期高点日期']
                code = str(row.get('股票代码', ''))
                name = str(row.get('股票简称', ''))

                price = float(row.get('最新价', 0)) if pd.notna(row.get('最新价')) else 0
                change_pct = float(row.get('涨跌幅', 0)) if pd.notna(row.get('涨跌幅')) else 0
                amount = float(row.get('换手率', 0)) if pd.notna(row.get('换手率')) else 0

                if not code or not name:
                    continue

                stock_data_list.append({
                    'code': code,
                    'name': name,
                    'price': price,
                    'change_pct': change_pct,
                    'amount': amount,
                    'sector': ''
                })

            # 批量获取或创建股票记录
            stock_cache = {}
            for stock_data in stock_data_list:
                if stock_data['code'] not in stock_cache:
                    stock_id = self._get_or_create_stock(stock_data['code'], stock_data['name'], stock_data['sector'])
                    stock_cache[stock_data['code']] = stock_id

            # 批量保存数据
            conn = self._get_connection()
            cursor = conn.cursor()

            now_iso = datetime.now().isoformat()

            for rank, stock_data in enumerate(stock_data_list, 1):
                stock_id = stock_cache.get(stock_data['code'])
                if stock_id < 0:
                    continue

                reason = source_name  # 高新股票直接用数据源名称作为reason

                to_save_data.append((
                    source_id, stock_id, today, rank, stock_data['price'],
                    stock_data['change_pct'], stock_data['amount'], stock_data['sector'],
                    reason, now_iso, now_iso
                ))

            if to_save_data:
                cursor.executemany('''
                    INSERT OR REPLACE INTO popularity_stocks
                    (source_id, stock_id, trade_date, rank, price, change_percent, amount, sector, reason, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', to_save_data)

            conn.commit()
            conn.close()

            record_count = len(to_save_data)
            logger.info(f"人气排行榜 {source_name} 保存完成: {record_count} 条")
            results["success"] = True
            results["message"] = f"成功保存 {source_name}: {record_count} 条"
            results["record_count"] = record_count

            return results

        except Exception as e:
            logger.error(f"获取并保存人气排行榜失败: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"系统错误: {e}",
                "source_name": source_name,
                "record_count": 0
            }

    def fetch_and_save_amount_ranking(self, amount_type: str, trade_date: str = None) -> Dict:
        """获取并保存成交额排行榜数据

        Args:
            amount_type: 类型名称（'竞价成交额', '全天成交额'）
            trade_date: 交易日期（格式: YYYY-MM-DD），不传则使用当前日期

        Returns:
            {
                "success": True/False,
                "message": "执行结果描述",
                "type_name": "类型名称",
                "record_count": 保存的记录数
            }
        """
        try:
            results = {
                "success": False,
                "message": "",
                "type_name": amount_type,
                "record_count": 0
            }

            logger.info(f"开始获取成交额排行榜: {amount_type}")

            # 使用指定的交易日期，不传则使用当前日期
            if trade_date:
                today = trade_date
            else:
                today = datetime.now().strftime("%Y-%m-%d")

            # 获取实时行情数据（带重试机制）
            max_retries = 3
            retry_delay = 2
            df = None

            for attempt in range(max_retries):
                try:
                    df = ak.stock_zh_a_spot_em()

                    if df is None or len(df) == 0:
                        logger.warning(f"未获取到实时行情数据 (尝试 {attempt + 1}/{max_retries})")
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay)
                            continue
                        results["message"] = "未获取到实时行情数据"
                        return results

                    # 按成交额排序取前20名
                    df_sorted = df.nlargest(20, '成交额')
                    break

                except Exception as e:
                    logger.error(f"获取实时行情数据失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    results["message"] = f"获取实时行情数据失败: {e}"
                    return results

            if df is None or df_sorted is None:
                logger.warning(f"获取实时行情数据失败: 达到最大重试次数 {max_retries}")
                results["message"] = f"获取实时行情数据失败: 达到最大重试次数 {max_retries}"
                return results

            logger.info(f"成功获取成交额数据: {len(df_sorted)} 条")

            # 获取或创建类型记录
            conn = self._get_connection()
            cursor = conn.cursor()

            # 设置查询时间
            query_time = "09:25:00" if amount_type == '竞价成交额' else "15:00:00"

            cursor.execute('SELECT type_id FROM amount_types WHERE type_name = ?', (amount_type,))
            result = cursor.fetchone()

            if result:
                type_id = result[0]
            else:
                cursor.execute('''
                    INSERT INTO amount_types (type_name, description, query_time, is_active, created_at, updated_at)
                    VALUES (?, 'AkShare API', ?, 1, ?, ?)
                ''', (amount_type, query_time, datetime.now().isoformat(), datetime.now().isoformat()))
                type_id = cursor.lastrowid
                conn.commit()

            conn.close()

            # 先收集所有数据
            stock_data_list = []

            for idx, (_, row) in enumerate(df_sorted.iterrows(), 1):
                code = str(row.get('代码', ''))
                name = str(row.get('名称', ''))
                price = float(row.get('最新价', 0)) if pd.notna(row.get('最新价')) else 0
                change_pct = float(row.get('涨跌幅', 0)) if pd.notna(row.get('涨跌幅')) else 0
                amount = float(row.get('成交额', 0)) if pd.notna(row.get('成交额')) else 0
                amount = amount / 100000000  # 转换为亿元

                sector = str(row.get('所属行业', ''))

                if not code or not name or amount <= 0:
                    continue

                stock_data_list.append({
                    'rank': idx,
                    'code': code,
                    'name': name,
                    'price': price,
                    'change_pct': change_pct,
                    'amount': amount,
                    'sector': sector
                })

            # 批量获取或创建股票记录
            stock_cache = {}
            for stock_data in stock_data_list:
                if stock_data['code'] not in stock_cache:
                    stock_id = self._get_or_create_stock(stock_data['code'], stock_data['name'], stock_data['sector'])
                    stock_cache[stock_data['code']] = stock_id

            # 批量保存数据
            conn = self._get_connection()
            cursor = conn.cursor()

            now_iso = datetime.now().isoformat()
            is_final = 1 if amount_type == '全天成交额' else 0

            to_save_data = []
            for stock_data in stock_data_list:
                stock_id = stock_cache.get(stock_data['code'])
                if stock_id < 0:
                    continue

                reason = f'成交额排第{stock_data["rank"]}位'
                to_save_data.append((
                    type_id, stock_id, today, stock_data['rank'], stock_data['price'],
                    stock_data['change_pct'], stock_data['amount'], stock_data['sector'],
                    reason, is_final, now_iso, now_iso
                ))

            if to_save_data:
                cursor.executemany('''
                    INSERT OR REPLACE INTO amount_stocks
                    (type_id, stock_id, trade_date, rank, price, change_percent, amount, sector, reason, is_final, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', to_save_data)

            conn.commit()
            conn.close()

            record_count = len(to_save_data)
            logger.info(f"成交额排行榜 {amount_type} 保存完成: {record_count} 条")
            results["success"] = True
            results["message"] = f"成功保存 {amount_type}: {record_count} 条"
            results["record_count"] = record_count

            return results

        except Exception as e:
            logger.error(f"获取并保存成交额排行榜失败: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"系统错误: {e}",
                "type_name": amount_type,
                "record_count": 0
            }

    async def fetch_and_save_strong_stocks(self, trade_date: str = None) -> Dict:
        """获取并保存强势股池数据

        数据源：Mairui API - hslt/qsgc (强势股池)
        - 获取指定日期的强势股池数据
        - 按热度类型（rx字段）分类存储
        - 热度类型包括：60日新高、近期多次涨停等

        Args:
            trade_date: 交易日期（格式: YYYY-MM-DD），不传则使用当前日期

        Returns:
            {
                "success": True/False,
                "message": "执行结果描述",
                "total_count": 总记录数,
                "hot_types": ["60日新高", "近期多次涨停", ...]
            }
        """
        import asyncio

        try:
            results = {
                "success": False,
                "message": "",
                "total_count": 0,
                "hot_types": []
            }

            if not MAIRUI_LICENCE:
                logger.warning("Mairui License 未配置，无法获取强势股池数据")
                results["message"] = "Mairui License 未配置"
                return results

            # 使用指定的交易日期，不传则使用当前日期
            if trade_date:
                today = trade_date
            else:
                today = datetime.now().strftime("%Y-%m-%d")

            logger.info(f"开始获取 {today} 的强势股池数据")

            # 调用 Mairui API
            def _fetch_strong_data():
                url = f"{MAIRUI_BASE_URL}/{MAIRUI_STRONG_API_URL}/{today}/{MAIRUI_LICENCE}"
                return mairui_get_with_retry(url)

            try:
                response = await asyncio.to_thread(_fetch_strong_data)

                if not response or response.status_code != 200:
                    logger.warning(f"Mairui 强势股池接口最终失败: {response.status_code if response else 'N/A'}")
                    results["message"] = f"Mairui API 错误: {response.status_code if response else 'N/A'}"
                    return results

                strong_data = response.json()

                if not isinstance(strong_data, list) or len(strong_data) == 0:
                    logger.warning(f"返回空数据: {today}")
                    results["message"] = "返回空数据（可能是非交易日或数据未更新）"
                    return results

                logger.info(f"✓ Mairui 强势股池数据获取成功: {len(strong_data)} 条")
                if strong_data:
                    logger.info(f"  强势股数据字段名: {list(strong_data[0].keys())}")

            except Exception as e:
                logger.warning(f"获取 Mairui 强势股池数据失败: {e}")
                results["message"] = f"接口调用失败: {str(e)}"
                return results

            # API调用成功，增加计数
            increment_mairui_api_usage()

            # 解析数据并按热度类型分组
            conn = self._get_connection()
            cursor = conn.cursor()

            # 初始化热度类型统计
            hot_type_groups = {}

            stock_cache = {}

            for stock in strong_data:
                try:
                    code = stock.get('dm', '')
                    name = stock.get('mc', '')

                    if not code or not name:
                        continue

                    hot_type = stock.get('rx', '未知')

                    if hot_type not in hot_type_groups:
                        hot_type_groups[hot_type] = []

                    price = float(stock.get('p', 0)) if stock.get('p') else 0
                    change_pct = float(stock.get('zf', 0)) if stock.get('zf') else 0
                    amount = float(stock.get('cje', 0)) if stock.get('cje') else 0
                    turnover_rate = float(stock.get('hs', 0)) if stock.get('hs') else 0
                    volume_ratio = float(stock.get('lb', 0)) if stock.get('lb') else 0
                    is_new_high = 1 if stock.get('nh') == '是' else 0

                    # 解析统计信息 tj (如 "1/1" 或 "2/2")
                    continuous_limit_days = 0
                    tj = stock.get('tj', '')
                    if tj and '/' in tj:
                        try:
                            continuous_limit_days = int(tj.split('/')[0])
                        except:
                            pass

                    sector = stock.get('hy', '')

                    stock_id = self._get_or_create_stock(code, name, sector, conn, cursor)

                    if stock_id < 0:
                        continue

                    hot_type_groups[hot_type].append({
                        'stock_id': stock_id,
                        'code': code,
                        'name': name,
                        'price': price,
                        'change_pct': change_pct,
                        'amount': amount,
                        'turnover_rate': turnover_rate,
                        'volume_ratio': volume_ratio,
                        'is_new_high': is_new_high,
                        'continuous_limit_days': continuous_limit_days,
                        'sector': sector,
                        'hot_type': hot_type
                    })

                except Exception as e:
                    logger.warning(f"处理强势股记录失败: code={stock.get('dm', '')}, error={e}")
                    continue

            # 保存数据到 strong_stocks 表
            total_saved = 0
            create_time = f"{today}T16:00:00"

            for hot_type, stock_list in hot_type_groups.items():
                logger.info(f"热度类型 {hot_type}: {len(stock_list)} 只")

                # 创建或更新热度类型记录
                cursor.execute('''
                    INSERT OR IGNORE INTO strong_stock_types
                    (type_name, description, sort_order, is_active, created_at, updated_at)
                    VALUES (?, ?, 0, 1, ?, ?)
                ''', (hot_type, f'强势股池 - {hot_type}', datetime.now().isoformat(), datetime.now().isoformat()))

                for rank, stock_data in enumerate(stock_list, 1):
                    try:
                        cursor.execute('''
                            INSERT OR REPLACE INTO strong_stocks
                            (stock_id, trade_date, hot_type, rank, price, change_percent, amount,
                             turnover_rate, volume_ratio, is_new_high, continuous_limit_days,
                             sector, reason, source, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            stock_data['stock_id'], today, hot_type, rank,
                            stock_data['price'], stock_data['change_pct'], stock_data['amount'],
                            stock_data['turnover_rate'], stock_data['volume_ratio'],
                            stock_data['is_new_high'], stock_data['continuous_limit_days'],
                            stock_data['sector'], f'{hot_type}强势股', 'mairui', create_time
                        ))
                        total_saved += 1
                    except Exception as e:
                        logger.warning(f"保存强势股记录失败: {hot_type}, code={stock_data['code']}, error={e}")
                        continue

            conn.commit()
            conn.close()

            logger.info(f"强势股池数据保存完成: 总计 {total_saved} 条，热度类型 {len(hot_type_groups)} 个")

            results["success"] = True
            results["message"] = f"成功保存强势股池数据: {total_saved} 条"
            results["total_count"] = total_saved
            results["hot_types"] = list(hot_type_groups.keys())

            return results

        except Exception as e:
            logger.error(f"获取并保存强势股池失败: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"系统错误: {e}",
                "total_count": 0,
                "hot_types": []
            }
