import sqlite3
import logging
import sqlite3
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import akshare as ak

logger = logging.getLogger(__name__)

DB_PATH = "data/fupan.db"


def is_trading_day(date_str: str) -> bool:
    """判断指定日期是否是交易日

    Args:
        date_str: 日期字符串（格式：YYYY-MM-DD）

    Returns:
        True表示是交易日，False表示不是
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*)
            FROM trading_days
            WHERE date = ? AND is_active = 1
        ''', (date_str,))
        result = cursor.fetchone()
        conn.close()
        return result[0] > 0
    except Exception as e:
        logger.warning(f"判断交易日失败: {e}")
        return False


def get_latest_trading_date_from_db() -> Optional[str]:
    """从数据库中获取最新的交易日期（相对于今天的最近交易日）

    Returns:
        最新的交易日期字符串（格式：YYYY-MM-DD），如果没有数据则返回None
    """
    try:
        today_date = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 从 trading_days 表获取小于等于今天的最新交易日
        cursor.execute('''
            SELECT MAX(date)
            FROM trading_days
            WHERE date <= ? AND is_active = 1
        ''', (today_date,))

        result = cursor.fetchone()
        conn.close()

        if result and result[0]:
            return result[0]

        # 如果 trading_days 表没有数据，从 first_limits 表获取
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT MAX(limit_date)
            FROM first_limits
            WHERE limit_date <= ?
        ''', (today_date,))

        result = cursor.fetchone()
        conn.close()

        if result and result[0]:
            return result[0]
        return None
    except Exception as e:
        logger.error(f"从数据库获取最新交易日失败: {e}")
        return None


def fetch_trading_days_from_akshare() -> List[str]:
    """从 AkShare 获取交易日历（获取所有历史交易日）

    Returns:
        交易日列表（格式：YYYY-MM-DD）
    """
    try:
        logger.info("从 AkShare 获取交易日历...")
        df = ak.tool_trade_date_hist_sina()

        # akshare 返回的列名通常是 'trade_date'
        if 'trade_date' in df.columns:
            trading_days = df['trade_date'].tolist()
            # 确保日期格式为 YYYY-MM-DD
            trading_days = [d if isinstance(d, str) else d.strftime('%Y-%m-%d') for d in trading_days]
            logger.info(f"成功获取 {len(trading_days)} 个交易日")
            return trading_days
        else:
            logger.error(f"AkShare 返回数据格式异常，列名：{df.columns.tolist()}")
            return []
    except Exception as e:
        logger.error(f"从 AkShare 获取交易日历失败: {e}", exc_info=True)
        return []


def filter_trading_days_by_year(trading_days: List[str], year: int) -> List[str]:
    """从交易日列表中筛选出指定年份的交易日

    Args:
        trading_days: 交易日列表
        year: 年份，例如 2025

    Returns:
        指定年份的交易日列表
    """
    year_prefix = f"{year}-"
    return [d for d in trading_days if d.startswith(year_prefix)]


def save_trading_days_to_db(trading_days: List[str]) -> int:
    """保存交易日到数据库

    Args:
        trading_days: 交易日列表（格式：YYYY-MM-DD）

    Returns:
        保存的记录数
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        saved_count = 0

        for date_str in trading_days:
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO trading_days (date, is_active, created_at, updated_at)
                    VALUES (?, 1, ?, ?)
                ''', (date_str, now, now))
                if cursor.rowcount > 0:
                    saved_count += 1
            except Exception as e:
                logger.warning(f"保存交易日 {date_str} 失败: {e}")

        conn.commit()
        conn.close()
        logger.info(f"成功保存 {saved_count} 个交易日到数据库")
        return saved_count
    except Exception as e:
        logger.error(f"保存交易日到数据库失败: {e}", exc_info=True)
        return 0


def fetch_and_save_trading_days() -> Tuple[int, List[str]]:
    """获取并保存交易日到数据库（获取今年、去年、明年的交易日）

    Returns:
        (保存的数量, 获取到的交易日列表)
    """
    try:
        # 获取所有历史交易日
        all_trading_days = fetch_trading_days_from_akshare()

        if not all_trading_days:
            logger.error("无法获取交易日历")
            return (0, [])

        # 筛选最近3年的交易日
        current_year = datetime.now().year
        years = [current_year - 1, current_year, current_year + 1]

        filtered_trading_days = []
        for year in years:
            year_days = filter_trading_days_by_year(all_trading_days, year)
            filtered_trading_days.extend(year_days)

        logger.info(f"筛选出 {len(filtered_trading_days)} 个交易日（{years[0]}年到{years[-1]}年）")

        # 保存到数据库
        saved = save_trading_days_to_db(filtered_trading_days)

        return (saved, filtered_trading_days)
    except Exception as e:
        logger.error(f"获取并保存交易日失败: {e}", exc_info=True)
        return (0, [])


def ensure_trading_day_exists(date_str: str) -> bool:
    """确保指定日期在交易日表中存在（如果不存在则添加）

    Args:
        date_str: 日期字符串（格式：YYYY-MM-DD）

    Returns:
        是否添加成功（或已存在）
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('SELECT date FROM trading_days WHERE date = ?', (date_str,))
        exists = cursor.fetchone()

        if exists:
            conn.close()
            return True

        # 如果不存在，添加该交易日
        now = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO trading_days (date, is_active, created_at, updated_at)
            VALUES (?, 1, ?, ?)
        ''', (date_str, now, now))

        conn.commit()
        conn.close()
        logger.info(f"添加新交易日到数据库: {date_str}")
        return True
    except Exception as e:
        logger.error(f"确保交易日存在失败: {e}", exc_info=True)
        return False


def get_trading_days_between(start_date: str, end_date: str) -> List[str]:
    """获取两个日期之间的所有交易日

    Args:
        start_date: 开始日期（格式：YYYY-MM-DD）
        end_date: 结束日期（格式：YYYY-MM-DD）

    Returns:
        交易日列表（格式：YYYY-MM-DD）
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT date
            FROM trading_days
            WHERE date BETWEEN ? AND ?
            AND is_active = 1
            ORDER BY date
        ''', (start_date, end_date))

        rows = cursor.fetchall()
        conn.close()

        return [row[0] for row in rows]
    except Exception as e:
        logger.error(f"获取日期范围内的交易日失败: {e}")
        return []


def get_recent_trading_days(count: int = 5, from_date: Optional[str] = None) -> List[str]:
    """获取最近的N个交易日（从指定日期开始向前查找）

    Args:
        count: 获取的交易日数量
        from_date: 指定日期（格式：YYYY-MM-DD），如果为None则使用当前日期

    Returns:
        交易日列表（格式：YYYY-MM-DD），按时间正序排列（从早到晚）
    """
    try:
        # 确定查询的基准日期
        if from_date is None:
            target_date = datetime.now()
        else:
            target_date = datetime.strptime(from_date, "%Y-%m-%d")

        target_date_str = target_date.strftime("%Y-%m-%d")

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 找到小于等于目标日期的最大交易日
        cursor.execute('''
            SELECT MAX(date)
            FROM trading_days
            WHERE date <= ? AND is_active = 1
        ''', (target_date_str,))

        result = cursor.fetchone()
        if not result or not result[0]:
            conn.close()
            return []

        latest_trading_date = result[0]

        # 从这个交易日开始，向前获取N个交易日
        cursor.execute('''
            SELECT date
            FROM trading_days
            WHERE date <= ? AND is_active = 1
            ORDER BY date DESC
            LIMIT ?
        ''', (latest_trading_date, count))

        rows = cursor.fetchall()
        conn.close()

        # 反转列表，使其按时间正序排列（从早到晚）
        return [row[0] for row in rows][::-1]
    except Exception as e:
        logger.error(f"获取最近交易日失败: {e}")
        return []


def get_last_trading_day(date_str: Optional[str] = None) -> str:
    """获取最近的一个交易日

    首先从数据库 trading_days 表查询，如果数据库为空则向后查找工作日

    Args:
        date_str: 日期字符串（格式：YYYY-MM-DD），如果为None则使用当前日期

    Returns:
        最近的交易日期字符串（格式：YYYY-MM-DD）
    """
    if date_str is None:
        target_date = datetime.now()
    else:
        target_date = datetime.strptime(date_str, "%Y-%m-%d")

    # 首先尝试从数据库查询最近的交易日
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 查询小于等于目标日期的最新交易日
        cursor.execute('''
            SELECT MAX(date)
            FROM trading_days
            WHERE date <= ? AND is_active = 1
        ''', (target_date.strftime("%Y-%m-%d"),))

        result = cursor.fetchone()
        conn.close()

        if result and result[0]:
            return result[0]
    except Exception as e:
        logger.warning(f"从数据库查询交易日失败: {e}")

    # 如果数据库没有交易数据，使用简单的向后查找工作日
    while True:
        weekday = target_date.weekday()
        if weekday < 5:  # 0=周一, 4=周五
            return target_date.strftime("%Y-%m-%d")
        target_date -= timedelta(days=1)
    """获取最近的一个交易日

    首先从数据库 trading_days 表查询，如果数据库为空则向后查找工作日

    Args:
        date_str: 日期字符串（格式：YYYY-MM-DD），如果为None则使用当前日期

    Returns:
        最近的交易日期字符串（格式：YYYY-MM-DD）
    """
    if date_str is None:
        target_date = datetime.now()
    else:
        target_date = datetime.strptime(date_str, "%Y-%m-%d")

    # 首先尝试从数据库查询最近的交易日
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 查询小于等于目标日期的最新交易日
        cursor.execute('''
            SELECT MAX(date)
            FROM trading_days
            WHERE date <= ? AND is_active = 1
        ''', (target_date.strftime("%Y-%m-%d"),))

        result = cursor.fetchone()
        conn.close()

        if result and result[0]:
            return result[0]
    except Exception as e:
        logger.warning(f"从数据库查询交易日失败: {e}")

    # 如果数据库没有交易数据，使用简单的向后查找工作日
    while True:
        weekday = target_date.weekday()
        if weekday < 5:  # 0=周一, 4=周五
            return target_date.strftime("%Y-%m-%d")
        target_date -= timedelta(days=1)


STAGE_COLOR_MAP = {
    'startup': {'name': '启动', 'bg_color': '#2ecc71'},
    'explosion': {'name': '爆发', 'bg_color': '#e74c3c'},
    'maintain': {'name': '维持', 'bg_color': '#3498db'},
    'divergence': {'name': '分歧', 'bg_color': '#9b59b6'},
    'recede': {'name': '退潮', 'bg_color': '#f39c12'},
    'backflow': {'name': '回流', 'bg_color': '#1abc9c'}
}


class RotationAnalysisDB:
    """题材轮动分析数据库操作类（新版表结构）"""

    def __init__(self):
        self.db_path = DB_PATH
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        return sqlite3.connect(self.db_path)
    
    def _ensure_topic_exists(self, topic_name: str) -> int:
        """确保题材存在，返回 topic_id"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT topic_id, is_active FROM topics WHERE topic_name = ?', (topic_name,))
        result = cursor.fetchone()
        
        if result:
            topic_id = result[0]
            conn.close()
            return topic_id
        
        # 创建新题材
        now = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO topics (topic_name, is_active, created_at, updated_at)
            VALUES (?, 1, ?, ?)
        ''', (topic_name, now, now))
        
        topic_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        if topic_id is None:
            raise RuntimeError("Failed to get topic_id after insert")
        
        logger.info(f"创建新题材: {topic_name} (id: {topic_id})")
        return topic_id
    
    def _get_topic_id_by_name(self, topic_name: str) -> Optional[int]:
        """根据题材名称获取 topic_id"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT topic_id FROM topics WHERE topic_name = ?', (topic_name,))
        result = cursor.fetchone()
        
        conn.close()
        return result[0] if result else None
    
    def _get_topic_name_by_id(self, topic_id: int) -> Optional[str]:
        """根据 topic_id 获取题材名称"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT topic_name FROM topics WHERE topic_id = ?', (topic_id,))
        result = cursor.fetchone()
        
        conn.close()
        return result[0] if result else None
    
    def save_analysis(self, topic: str, content: str, date: str, timestamp: Optional[str] = None, stage: Optional[str] = None):
        """保存或更新分析记录"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if timestamp is None:
                timestamp = datetime.now().isoformat()

            if date is None:
                date = datetime.now().strftime("%Y-%m-%d")

            topic_id = self._ensure_topic_exists(topic)
            is_active = 1 if content and content.strip() else 0

            cursor.execute('''
                INSERT OR REPLACE INTO rotation_actives
                (topic_id, content, date, timestamp, is_active, stage)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (topic_id, content, date, timestamp, is_active, stage))

            # 更新题材的 updated_at
            cursor.execute('''
                UPDATE topics SET updated_at = ? WHERE topic_id = ?
            ''', (timestamp, topic_id))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"保存分析记录失败: {e}")
            return False

    def update_topic_stage(self, topic: str, stage: Optional[str], date: str) -> bool:
        """更新题材的阶段状态"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            topic_id = self._ensure_topic_exists(topic)

            if stage:
                cursor.execute('''
                    UPDATE rotation_actives
                    SET stage = ?
                    WHERE topic_id = ? AND date = ?
                ''', (stage, topic_id, date))
            else:
                cursor.execute('''
                    UPDATE rotation_actives
                    SET stage = NULL
                    WHERE topic_id = ? AND date = ?
                ''', (topic_id, date))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"更新题材状态失败: {e}")
            return False

    def get_records_by_date(self, date_str: str) -> List[Dict]:
        """根据绝对日期查询记录"""
        try:
            today = datetime.now().date()
            record_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            calculated_day = (record_date - today).days

            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT
                    t.topic_id,
                    t.topic_name,
                    ra.content,
                    ra.date,
                    ra.timestamp,
                    ra.is_active,
                    ra.stage
                FROM rotation_actives ra
                JOIN topics t ON ra.topic_id = t.topic_id
                WHERE ra.date = ?
                ORDER BY t.topic_name
            ''', (date_str,))

            records = []
            for row in cursor.fetchall():
                records.append({
                    'topic_id': row[0],
                    'topic': row[1],
                    'content': row[2],
                    'date': row[3],
                    'timestamp': row[4],
                    'is_active': row[5],
                    'stage': row[6]
                })

            conn.close()
            return records
        except Exception as e:
            logger.error(f"根据日期查询记录失败: {e}")
            return []
    
    def get_records_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        """根据日期范围查询记录"""
        try:
            today = datetime.now().date()

            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT
                    t.topic_id,
                    t.topic_name,
                    ra.content,
                    ra.date,
                    ra.timestamp,
                    ra.is_active,
                    ra.stage
                FROM rotation_actives ra
                JOIN topics t ON ra.topic_id = t.topic_id
                WHERE ra.date BETWEEN ? AND ?
                  AND ra.content IS NOT NULL
                  AND ra.content != ''
                ORDER BY ra.date, ra.is_active DESC, t.topic_name
            ''', (start_date, end_date))

            records = []
            for row in cursor.fetchall():
                record_date = datetime.strptime(row[3], "%Y-%m-%d").date()
                calculated_day = (record_date - today).days

                records.append({
                    'topic_id': row[0],
                    'topic': row[1],
                    'content': row[2],
                    'date': row[3],
                    'timestamp': row[4],
                    'is_active': row[5],
                    'stage': row[6]
                })

            conn.close()
            return records
        except Exception as e:
            logger.error(f"查询日期范围记录失败: {e}")
            return []
    
    def remove_analysis(self, topic: str, date: str):
        """删除分析记录"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            topic_id = self._get_topic_id_by_name(topic)
            if not topic_id:
                logger.warning(f"题材不存在: {topic}")
                conn.close()
                return False

            cursor.execute('''
                DELETE FROM rotation_actives
                WHERE topic_id = ? AND date = ?
            ''', (topic_id, date))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"删除分析记录失败: {e}")
            return False

    def get_all_analyses(self) -> List[Dict]:
        """获取所有分析记录"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT
                    t.topic_id,
                    t.topic_name,
                    ra.content,
                    ra.date,
                    ra.timestamp
                FROM rotation_actives ra
                JOIN topics t ON ra.topic_id = t.topic_id
                ORDER BY t.topic_name, ra.date
            ''')

            analyses = []
            for row in cursor.fetchall():
                analyses.append({
                    'topic_id': row[0],
                    'topic': row[1],
                    'content': row[2],
                    'date': row[3],
                    'timestamp': row[4]
                })

            conn.close()
            return analyses
        except Exception as e:
            logger.error(f"获取分析记录失败: {e}")
            return []
    
    def get_all_topics_with_analyses(self) -> List[Dict]:
        """获取所有题材及其分析记录"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT
                    t.topic_id,
                    t.topic_name,
                    ra.content,
                    ra.date,
                    ra.timestamp,
                    ra.is_active,
                    ra.stage
                FROM rotation_actives ra
                JOIN topics t ON ra.topic_id = t.topic_id
                ORDER BY ra.date, t.topic_name
            ''')

            today = datetime.now().date()
            topics_dict = {}

            for row in cursor.fetchall():
                topic_id = row[0]
                topic = row[1]
                content = row[2]
                date_str = row[3]
                timestamp = row[4]
                is_active = row[5]
                stage = row[6] if len(row) > 6 else None

                if topic not in topics_dict:
                    topics_dict[topic] = {
                        'topic_id': topic_id,
                        'name': topic,
                        'days': {},
                        'stages': {},
                        'hot': is_active == 1,
                        'has_placeholder': is_active == 0
                    }

                topics_dict[topic]['days'][date_str] = content
                topics_dict[topic]['stages'][date_str] = stage
                topics_dict[topic]['hot'] = topics_dict[topic]['hot'] or (is_active == 1)
                topics_dict[topic]['has_placeholder'] = topics_dict[topic]['has_placeholder'] and (is_active == 0)

            conn.close()

            return list(topics_dict.values())
        except Exception as e:
            logger.error(f"获取题材数据失败: {e}")
            return []
    
    def get_all_topics(self) -> List[Dict]:
        """获取所有题材列表，按最后活跃时间排序"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT
                    t.topic_id,
                    t.topic_name,
                    t.is_active,
                    t.created_at,
                    t.updated_at,
                    COUNT(ra.id) as record_count,
                    COALESCE(MAX(ta.activation_date), t.updated_at) as last_active_date
                FROM topics t
                LEFT JOIN rotation_actives ra ON t.topic_id = ra.topic_id AND ra.content IS NOT NULL AND ra.content != ''
                LEFT JOIN topic_activations ta ON t.topic_id = ta.topic_id
                GROUP BY t.topic_id
                ORDER BY last_active_date DESC
            ''')

            topics = []
            for row in cursor.fetchall():
                topics.append({
                    'topic_id': row[0],
                    'name': row[1],
                    'is_active': bool(row[2]),
                    'created_at': row[3],
                    'last_update': row[6],  # 使用最后活跃日期
                    'record_count': row[5] or 0
                })

            conn.close()
            return topics
        except Exception as e:
            logger.error(f"获取题材列表失败: {e}")
            return []
    
    def get_topic_stocks(self, topic_id: int, day: Optional[int] = None, relation_type: Optional[str] = None) -> List[Dict]:
        """获取题材关联的标的（预留接口）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            query = '''
                SELECT
                    s.stock_id,
                    s.stock_code,
                    s.stock_name,
                    tsr.relation_type,
                    tsr.date,
                    tsr.is_active
                FROM topic_stock_relations tsr
                JOIN stocks s ON tsr.stock_id = s.stock_id
                WHERE tsr.topic_id = ?
            '''
            params: List = [topic_id]
            
            if day is not None:
                query += ' AND tsr.day = ?'
                params.append(day)
            
            if relation_type is not None:
                query += ' AND tsr.relation_type = ?'
                params.append(relation_type)
            
            query += ' ORDER BY tsr.date DESC, s.stock_code'
            
            cursor.execute(query, params)
            
            stocks = []
            for row in cursor.fetchall():
                stocks.append({
                    'stock_id': row[0],
                    'code': row[1],
                    'name': row[2],
                    'relation_type': row[3],
                    'date': row[4],
                    'is_active': bool(row[5])
                })
            
            conn.close()
            return stocks
        except Exception as e:
            logger.error(f"获取题材标的失败: {e}")
            return []
    
    def get_first_limits_by_date(self, date_str: str, table: str = None) -> List[Dict]:
        """获取指定日期的首板数据

        参数:
        - date_str: 查询日期
        - table: 指定表名（None=自动判断）
                - "first_limits" 或 "first_limits_tmp"
        """
        # 如果未指定表，根据时间自动判断
        if table is None:
            from src.main import is_in_trading_hours
            table = "first_limits_tmp" if is_in_trading_hours() else "first_limits"

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(f'''
                SELECT
                    fl.id,
                    fl.stock_id,
                    s.stock_code,
                    s.stock_name,
                    s.industry,
                    fl.limit_date,
                    fl.first_limit_time,
                    fl.final_limit_time,
                    fl.limit_price,
                    fl.limit_type,
                    fl.amount,
                    fl.reason,
                    coalesce(fl.is_exploded, 0) as is_exploded
                FROM {table} fl
                JOIN stocks s ON fl.stock_id = s.stock_id
                WHERE fl.limit_date = ?
                ORDER BY fl.first_limit_time
            ''', (date_str,))

            records = []
            for row in cursor.fetchall():
                records.append({
                    'id': row[0],
                    'stock_id': row[1],
                    'code': row[2],
                    'name': row[3],
                    'sector': row[4],  # 使用 industry 映射到 sector
                    'limit_date': row[5],
                    'first_time': row[6],
                    'final_time': row[7],
                    'price': row[8],
                    'limit_type': row[9],
                    'amount': row[10],
                    'reason': row[11],
                    'is_exploded': bool(row[12])
                })

            conn.close()
            return records
        except Exception as e:
            logger.error(f"获取首板数据失败: {e}")
            return []

    def get_or_create_stock(self, stock_code: str, stock_name: str, industry: str = '') -> int:
        """获取或创建股票记录，返回 stock_id"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 检查是否已存在
            cursor.execute('SELECT stock_id FROM stocks WHERE stock_code = ?', (stock_code,))
            result = cursor.fetchone()

            if result:
                stock_id = result[0]
                # 更新股票名称和行业（只在industry为空时才更新）
                cursor.execute('''
                    UPDATE stocks
                    SET stock_name = ?,
                        industry = CASE WHEN industry IS NULL OR industry = '' THEN ? ELSE industry END,
                        updated_at = ?
                    WHERE stock_id = ?
                ''', (stock_name, industry, datetime.now().isoformat(), stock_id))
            else:
                # 创建新股票记录
                cursor.execute('''
                    INSERT INTO stocks (stock_code, stock_name, industry, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (stock_code, stock_name, industry, datetime.now().isoformat(), datetime.now().isoformat()))

                stock_id = cursor.lastrowid

            conn.commit()
            conn.close()
            return stock_id
        except Exception as e:
            logger.error(f"获取或创建股票失败: {e}")
            return -1

    def save_first_limit(self, stock_code: str, stock_name: str, industry: str,
                          limit_date: str, first_limit_time: str, limit_price: float,
                          amount: float, limit_type: str = '10%', reason: str = '首板涨停') -> bool:
        """保存首板数据到数据库"""
        try:
            # 获取或创建股票
            stock_id = self.get_or_create_stock(stock_code, stock_name, industry)
            if stock_id < 0:
                return False

            # 确保使用交易日（非交易日转为最近的一个交易日）
            trading_date = get_last_trading_day(limit_date)

            conn = self._get_connection()
            cursor = conn.cursor()

            # 检查当天是否已有该股票的首板记录
            cursor.execute('''
                SELECT id FROM first_limits
                WHERE stock_id = ? AND limit_date = ?
            ''', (stock_id, trading_date))

            if cursor.fetchone():
                conn.close()
                return True

            # 插入首板记录
            cursor.execute('''
                INSERT INTO first_limits (
                    stock_id, limit_date, first_limit_time, limit_price,
                    amount, reason, limit_type, is_exploded, source, create_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'akshare', ?)
            ''', (stock_id, trading_date, first_limit_time, limit_price,
                  amount, reason, limit_type, datetime.now().isoformat()))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"保存首板数据失败: {e}")
            return False

    def add_topic_stock(self, topic_id: int, stock_id: int, day: int, relation_type: str) -> bool:
        """添加标的到题材（预留接口）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 计算实际日期
            today = datetime.now()
            target_date = today + timedelta(days=day)
            date_str = target_date.strftime("%Y-%m-%d")
            
            cursor.execute('''
                INSERT OR REPLACE INTO topic_stock_relations 
                (topic_id, stock_id, day, date, relation_type, is_active, create_time)
                VALUES (?, ?, ?, ?, ?, 1, ?)
            ''', (topic_id, stock_id, day, date_str, relation_type, datetime.now().isoformat()))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"添加题材标的失败: {e}")
            return False
    
    def remove_topic_stock(self, topic_id: int, stock_id: int, day: int) -> bool:
        """从题材移除标的（预留接口）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                DELETE FROM topic_stock_relations
                WHERE topic_id = ? AND stock_id = ? AND day = ?
            ''', (topic_id, stock_id, day))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"移除题材标的失败: {e}")
            return False

    def add_first_limit_to_topic(self, stock_id: int, topic_id: int, date: str, association_date: str) -> bool:
        """将首板关联到题材

        首板板块核心逻辑（重要！）：
        1. 用户拖拽标的到题材时，前端传递displayTradeDate作为association_date
        2. topic_stock_relations表：记录标的所属题材（持续有效，不考虑日期）
        3. first_limit_topics表：记录该首板在该交易日关联的题材（按association_date区分）
        4. create_time：用户操作时间（如周日操作，记录周日时间戳）
        5. association_date：该首板与题材的真实活跃交易日（如周五的首板，记录周五日期）

        参数说明：
        - date: 前端传递的日期（已废弃，保留兼容性）
        - association_date: 前端传递的displayTradeDate（该首板与题材的真实交易日）

        查询逻辑：
        - 先查询topic_activations获取当天激活的主题ID列表
        - 再查询first_limit_topics JOIN first_limits
        - 过滤条件：topic_id IN (激活的ID列表) AND first_limits.limit_date = association_date
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 获取首板记录的 first_limit_id（用于记录到 topic_stock_relations）
            # 盘中数据可能在 first_limits_tmp 表中，先查询正式表，再查询临时表
            cursor.execute('''
                SELECT id FROM first_limits WHERE stock_id = ? AND limit_date = ?
            ''', (stock_id, association_date))
            result = cursor.fetchone()

            # 如果正式表中没找到，尝试从临时表查询（盘中临时数据）
            if not result:
                cursor.execute('''
                    SELECT id FROM first_limits_tmp WHERE stock_id = ? AND limit_date = ?
                ''', (stock_id, association_date))
                result = cursor.fetchone()

            if not result:
                logger.error(f"未找到首板记录: stock_id={stock_id}, limit_date={association_date}")
                conn.close()
                return False

            first_limit_id = result[0]

            logger.info(f"关联首板到题材: stock_id={stock_id}, first_limit_id={first_limit_id}, topic_id={topic_id}, association_date={association_date}")

            # 检查 first_limit_topics 是否已存在该日期的关联（使用 stock_id + topic_id + association_date 判断重复）
            cursor.execute('''
                SELECT id FROM first_limit_topics
                WHERE stock_id = ? AND topic_id = ? AND association_date = ?
            ''', (stock_id, topic_id, association_date))

            if cursor.fetchone():
                logger.info(f"first_limit_topics关联已存在: stock_id={stock_id}, topic_id={topic_id}, association_date={association_date}")
            else:
                # 插入到 first_limit_topics（记录首板-题材关联，create_time为操作时间，association_date为交易日）
                cursor.execute('''
                    INSERT INTO first_limit_topics
                    (stock_id, first_limit_id, topic_id, create_time, association_date)
                    VALUES (?, ?, ?, ?, ?)
                ''', (stock_id, first_limit_id, topic_id, datetime.now().isoformat(), association_date))
                logger.info(f"插入first_limit_topics: stock_id={stock_id}, first_limit_id={first_limit_id}, topic_id={topic_id}, association_date={association_date}")

            # 盘中关联：同时写入临时表
            from src.main import is_in_trading_hours
            if is_in_trading_hours():
                cursor.execute('''
                    SELECT id FROM first_limit_topics_tmp
                    WHERE stock_id = ? AND topic_id = ? AND association_date = ?
                ''', (stock_id, topic_id, association_date))

                if cursor.fetchone():
                    logger.info(f"first_limit_topics_tmp关联已存在: stock_id={stock_id}, topic_id={topic_id}, association_date={association_date}")
                else:
                    cursor.execute('''
                        INSERT INTO first_limit_topics_tmp
                        (stock_id, first_limit_id, topic_id, create_time, association_date)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (stock_id, first_limit_id, topic_id, datetime.now().isoformat(), association_date))
                    logger.info(f"插入first_limit_topics_tmp: stock_id={stock_id}, first_limit_id={first_limit_id}, topic_id={topic_id}, association_date={association_date}")

            # 插入到 topic_stock_relations（记录标的所属题材，持续有效，不区分日期）
            # 检查是否已存在该标的和题材的任意关联（不考虑日期）
            cursor.execute('''
                SELECT id FROM topic_stock_relations
                WHERE stock_id = ? AND topic_id = ?
            ''', (stock_id, topic_id))

            if cursor.fetchone():
                logger.info(f"topic_stock_relations关联已存在: stock_id={stock_id}, topic_id={topic_id}")
            else:
                cursor.execute('''
                    INSERT INTO topic_stock_relations
                    (topic_id, stock_id, first_limit_id, date, relation_type, is_active, create_time)
                    VALUES (?, ?, ?, ?, 'first_limit', 1, ?)
                ''', (topic_id, stock_id, first_limit_id, association_date, datetime.now().isoformat()))
                logger.info(f"插入topic_stock_relations: stock_id={stock_id}, topic_id={topic_id}, date={association_date}")

            conn.commit()
            conn.close()

            logger.info(f"成功关联首板到题材: first_limit_id={first_limit_id}, topic_id={topic_id}, association_date={association_date}")
            return True
        except Exception as e:
            logger.error(f"添加首板-题材关联失败: {e}")
            return False

    def get_first_limit_topics(self, first_limit_id: int) -> List[Dict]:
        """获取首板关联的题材（从 first_limit_topics 表查询）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT
                    t.topic_id,
                    t.topic_name
                FROM first_limit_topics flt
                JOIN topics t ON flt.topic_id = t.topic_id
                WHERE flt.first_limit_id = ?
                ORDER BY t.topic_name
            ''', (first_limit_id,))

            topics = []
            for row in cursor.fetchall():
                topics.append({
                    'topic_id': row[0],
                    'name': row[1]
                })

            conn.close()
            return topics
        except Exception as e:
            logger.error(f"获取首板题材关联失败: {e}")
            return []

    def get_stock_id_by_first_limit_id(self, first_limit_id: int) -> Optional[int]:
        """获取首板对应的股票ID

        首板板块核心逻辑（重要！）：
        - 用于删除首板-题材关联时，判断是否需要删除 topic_stock_relations
        - 根据首板ID查询对应的股票ID

        参数说明：
        - first_limit_id: 首板ID

        返回值：
        - 股票ID，如果找不到则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT stock_id
                FROM first_limits
                WHERE id = ?
            ''', (first_limit_id,))

            result = cursor.fetchone()
            conn.close()

            return result[0] if result else None
        except Exception as e:
            logger.error(f"获取首板的股票ID失败: {e}")
            return None

    def remove_topic_stock_relation(self, topic_id: int, stock_id: int) -> bool:
        """删除题材-标的长期关联关系

        首板板块核心逻辑（重要！）：
        - 只删除 topic_stock_relations 表中的记录（持久关联表）
        - 不删除 first_limit_topics 表中的记录（短期时效表）
        - 用于用户确认删除时，解除该标的与此题材的长期关联

        参数说明：
        - topic_id: 题材ID
        - stock_id: 股票ID

        删除条件：
        - topic_id AND stock_id（不考虑日期，删除所有关联）
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                DELETE FROM topic_stock_relations
                WHERE topic_id = ? AND stock_id = ?
            ''', (topic_id, stock_id))
            affected_rows = cursor.rowcount

            conn.commit()
            conn.close()

            if affected_rows > 0:
                logger.info(f"成功删除题材-标的长期关联: topic_id={topic_id}, stock_id={stock_id}")
            else:
                logger.warning(f"未找到题材-标的长期关联: topic_id={topic_id}, stock_id={stock_id}")

            return affected_rows > 0
        except Exception as e:
            logger.error(f"删除题材-标的长期关联失败: {e}")
            return False
            logger.error(f"获取首板题材关联失败: {e}")
            return []

    def remove_first_limit_topic(self, stock_id: int, topic_id: int, association_date: str) -> bool:
        """移除首板-题材关联

        首板板块核心逻辑（重要！）：
        - 只删除first_limit_topics表中 association_date 指定日期的记录
        - 不删除topic_stock_relations表中的关联（持久关联表，由其他页面管理）

        参数说明：
        - stock_id: 股票ID（已从 first_limit_id 迁移）
        - association_date: 该首板与题材的关联日期（从全局displayTradeDate获取）

        删除条件：
        - stock_id AND topic_id AND association_date
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 从 first_limit_topics 删除（按association_date + stock_id定位）
            cursor.execute('''
                DELETE FROM first_limit_topics
                WHERE stock_id = ? AND topic_id = ? AND association_date = ?
            ''', (stock_id, topic_id, association_date))
            affected_rows = cursor.rowcount

            conn.commit()
            conn.close()

            if affected_rows > 0:
                logger.info(f"成功移除首板-题材关联: stock_id={stock_id}, topic_id={topic_id}, association_date={association_date}")
            else:
                logger.warning(f"未找到首板-题材关联: stock_id={stock_id}, topic_id={topic_id}, association_date={association_date}")

            return affected_rows > 0
        except Exception as e:
            logger.error(f"移除首板-题材关联失败: {e}")
            return False

    def activate_topic(self, topic_name: str, date: str) -> Tuple[bool, int, str]:
        """激活题材（显示卡片）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 检查题材是否存在
            cursor.execute('SELECT topic_id FROM topics WHERE topic_name = ?', (topic_name,))
            result = cursor.fetchone()

            if result:
                topic_id = result[0]
                logger.info(f"题材已存在: {topic_name} (ID: {topic_id})")
            else:
                # 创建新题材
                cursor.execute('''
                    INSERT INTO topics (topic_name, created_at, updated_at)
                    VALUES (?, ?, ?)
                ''', (topic_name, datetime.now().isoformat(), datetime.now().isoformat()))

                topic_id = cursor.lastrowid
                logger.info(f"创建新题材: {topic_name} (ID: {topic_id})")

            # 检查今日是否已激活
            cursor.execute('''
                SELECT id FROM topic_activations
                WHERE topic_id = ? AND activation_date = ?
            ''', (topic_id, date))

            if cursor.fetchone():
                conn.close()
                logger.warning(f"题材今日已激活: {topic_name} (日期: {date})")
                return (False, topic_id, "今日已激活")

            # 插入激活记录
            cursor.execute('''
                INSERT INTO topic_activations (topic_id, activation_date, is_active, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?)
            ''', (topic_id, date, datetime.now().isoformat(), datetime.now().isoformat()))

            conn.commit()
            conn.close()

            logger.info(f"成功激活题材: {topic_name} (日期: {date})")
            return (True, topic_id, "激活成功")

        except Exception as e:
            logger.error(f"激活题材失败: {e}")
            return (False, -1, str(e))

    def check_topic_stock_relations(self, topic_id: int) -> List[Dict]:
        """检查题材在topic_stock_relations表中的持久关联
        
        今日首板删除题材的逻辑（重要！）：
        - 只检查topic_stock_relations表中的持久关联
        - 不检查first_limit_topics表中的临时关联
        - 如果stock_count > 0，则不能删除整个题材
        - 避免删除题材后，其他日期的first_limit_topics中的topic_id变成孤儿引用
        
        参数说明：
        - topic_id: 题材ID
        
        返回值：
        - 关联的股票列表
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 
                    tsr.stock_id,
                    s.stock_code,
                    s.stock_name,
                    tsr.relation_type,
                    tsr.date,
                    tsr.is_active,
                    tsr.create_time
                FROM topic_stock_relations tsr
                LEFT JOIN stocks s ON tsr.stock_id = s.stock_id
                WHERE tsr.topic_id = ?
            ''', (topic_id,))
            
            relations = []
            for row in cursor.fetchall():
                relations.append({
                    'stock_id': row[0],
                    'stock_code': row[1],
                    'stock_name': row[2],
                    'relation_type': row[3],
                    'date': row[4],
                    'is_active': bool(row[5]),
                    'create_time': row[6]
                })
            
            conn.close()
            return relations
        except Exception as e:
            logger.error(f"检查题材持久关联失败: {e}")
            return []

    def get_topic_active_stocks(self, topic_name: str, days: int = 24) -> List[Dict]:
        """获取题材近期活跃标的（近N个交易日有过涨停的股票）

        参数说明：
        - topic_name: 题材名称
        - days: 近多少个交易日（默认24个）

        返回值：
        - 每个标的的详细信息列表：
            - stock_code: 股票代码
            - stock_name: 股票名称
            - limit_count: 近期涨停次数
            - max_continuous_days: 最高连板高度
            - new_high_count: 新高次数（无新高则不返回该字段）
            - recent_new_high_date: 最近新高日期（source_id=9时才有该字段）
            - recent_limit_date: 最近涨停日期
            - first_association_date: 近期最早涨停日期
        """
        try:
            logger.info(f"[DEBUG] get_topic_active_stocks 开始: topic_name={topic_name}, days={days}")
            conn = self._get_connection()
            cursor = conn.cursor()

            # 获取题材ID
            cursor.execute('SELECT topic_id FROM topics WHERE topic_name = ?', (topic_name,))
            topic_result = cursor.fetchone()
            if not topic_result:
                logger.warning(f"[DEBUG] 题材不存在: topic_name={topic_name}")
                conn.close()
                return []
            topic_id = topic_result[0]
            logger.info(f"[DEBUG] 找到题材: topic_id={topic_id}")

            # 获取该题材的股票代码及其最早关联日期
            cursor.execute('''
                SELECT
                    s.stock_code,
                    s.stock_name,
                    s.stock_id,
                    MIN(flt.association_date) as first_association_date
                FROM first_limit_topics flt
                JOIN stocks s ON flt.stock_id = s.stock_id
                WHERE flt.topic_id = ?
                GROUP BY s.stock_id, s.stock_code, s.stock_name
            ''', (topic_id,))
            topic_stocks = cursor.fetchall()
            logger.info(f"[DEBUG] 题材关联的股票数: {len(topic_stocks)}")
            if topic_stocks:
                stock_codes = [s[0] for s in topic_stocks]
                logger.info(f"[DEBUG] 股票代码列表前10个: {stock_codes[:10]}")

            if not topic_stocks:
                logger.warning(f"[DEBUG] 题材没有关联的股票")
                conn.close()
                return []

            # 获取first_limits表中存在的最近N个交易日
            cursor.execute('''
                SELECT DISTINCT limit_date FROM first_limits
                ORDER BY limit_date DESC
                LIMIT ?
            ''', (days,))
            trading_dates = [row[0] for row in cursor.fetchall()]
            logger.info(f"[DEBUG] trading_dates: {len(trading_dates)}个, 日期范围: {trading_dates[0] if trading_dates else 'None'} 到 {trading_dates[-1] if trading_dates else 'None'}")

            if not trading_dates:
                logger.warning(f"[DEBUG] 没有首板数据")
                conn.close()
                return []

            min_date = min(trading_dates)
            max_date = max(trading_dates)
            logger.info(f"[DEBUG] 日期范围: min_date={min_date}, max_date={max_date}")

            # 查询该题材的股票在近N个交易日内的涨停记录
            stock_ids = [s[2] for s in topic_stocks]
            placeholders = ','.join(['?' for _ in stock_ids])

            cursor.execute(f'''
                SELECT
                    s.stock_id,
                    COUNT(DISTINCT cl.trade_date) as limit_count
                FROM continuous_limits_history cl
                JOIN stocks s ON cl.code = s.stock_code
                WHERE s.stock_id IN ({placeholders})
                  AND cl.trade_date BETWEEN ? AND ?
                GROUP BY s.stock_id
                HAVING COUNT(DISTINCT cl.trade_date) > 0
            ''', stock_ids + [min_date, max_date])

            limit_stats = {row[0]: row[1] for row in cursor.fetchall()}
            logger.info(f"[DEBUG] 最近有涨停的股票数: {len(limit_stats)}, limit_stats={limit_stats}")

            if not limit_stats:
                logger.warning(f"[DEBUG] 没有股票在近期有涨停记录")
                conn.close()
                return []

            # 查询这些股票的连板数据
            cursor.execute(f'''
                SELECT
                    cl.code,
                    MAX(cl.continuous_days) as max_continuous_days
                FROM continuous_limits_history cl
                JOIN stocks s ON cl.code = s.stock_code
                WHERE s.stock_id IN ({placeholders})
                  AND cl.trade_date BETWEEN ? AND ?
                GROUP BY cl.code
            ''', stock_ids + [min_date, max_date])
            
            continuous_stats = {row[0]: row[1] for row in cursor.fetchall()}
            logger.info(f"[DEBUG] 有连板记录的股票数: {len(continuous_stats)}, continuous_stats={continuous_stats}")

            stocks = []
            for stock_code, stock_name, stock_id, _ in topic_stocks:
                 if stock_id not in limit_stats:
                     continue

                 logger.info(f"[DEBUG] 处理股票: stock_code={stock_code}, stock_name={stock_name}, stock_id={stock_id}")

                 stock_data = {
                     'stock_code': stock_code,
                     'stock_name': stock_name,
                     'stock_id': stock_id,
                     'limit_count': limit_stats[stock_id],
                     'max_continuous_days': continuous_stats.get(stock_code, 0)
                 }

                 cursor.execute(f'''
                     SELECT MIN(trade_date)
                     FROM continuous_limits_history cl
                     JOIN stocks s ON cl.code = s.stock_code
                     WHERE s.stock_id = ?
                       AND cl.trade_date BETWEEN ? AND ?
                 ''', (stock_id, min_date, max_date))
                 first_limit_result = cursor.fetchone()
                 if first_limit_result and first_limit_result[0]:
                     stock_data['first_association_date'] = first_limit_result[0]
                 else:
                     stock_data['first_association_date'] = None

                 cursor.execute(f'''
                     SELECT MAX(trade_date)
                     FROM continuous_limits_history cl
                     JOIN stocks s ON cl.code = s.stock_code
                     WHERE s.stock_id = ?
                       AND cl.trade_date BETWEEN ? AND ?
                 ''', (stock_id, min_date, max_date))
                 recent_limit_result = cursor.fetchone()
                 if recent_limit_result and recent_limit_result[0]:
                     stock_data['recent_limit_date'] = recent_limit_result[0]

                 cursor.execute('''
                     SELECT COUNT(*)
                     FROM popularity_stocks
                     WHERE stock_id = ?
                       AND source_id = 9
                       AND trade_date BETWEEN ? AND ?
                 ''', (stock_id, min_date, max_date))
                 new_high_result = cursor.fetchone()
                 new_high_count = new_high_result[0] if new_high_result else 0

                 if new_high_count > 0:
                     stock_data['new_high_count'] = new_high_count

                     cursor.execute('''
                         SELECT MAX(trade_date)
                         FROM popularity_stocks
                         WHERE stock_id = ?
                           AND source_id = 9
                           AND trade_date BETWEEN ? AND ?
                     ''', (stock_id, min_date, max_date))
                     recent_new_high_result = cursor.fetchone()
                     if recent_new_high_result and recent_new_high_result[0]:
                         stock_data['recent_new_high_date'] = recent_new_high_result[0]

                 stocks.append(stock_data)

            logger.info(f"[DEBUG] 最终返回股票数: {len(stocks)}")
            conn.close()
            return stocks
        except Exception as e:
            logger.error(f"获取题材活跃标的失败: {e}", exc_info=True)
            return []

    def get_topic_trend_stocks(self, topic_name: str, days: int = 24) -> Dict:
        """获取题材的趋势标历史（近N个交易日中的趋势标的入选数据）

        参数说明：
        - topic_name: 题材名称
        - days: 近多少个交易日（默认24个）

        返回值：
        - 按入选次数分组的趋势标数据
        """
        try:
            logger.info(f"[DEBUG] get_topic_trend_stocks 开始: topic_name={topic_name}, days={days}")
            conn = self._get_connection()
            cursor = conn.cursor()

            # 获取题材ID
            cursor.execute('SELECT topic_id FROM topics WHERE topic_name = ?', (topic_name,))
            topic_result = cursor.fetchone()
            if not topic_result:
                logger.warning(f"[DEBUG] 题材不存在: topic_name={topic_name}")
                conn.close()
                return {"by_count": {}, "by_date": {}}
            topic_id = topic_result[0]
            logger.info(f"[DEBUG] 找到题材: topic_id={topic_id}")

            # 从topic_stock_relations获取该题材关联的所有stock_id（持久关联）
            cursor.execute('''
                SELECT DISTINCT stock_id
                FROM topic_stock_relations
                WHERE topic_id = ?
            ''', (topic_id,))
            stock_ids = [row[0] for row in cursor.fetchall()]
            logger.info(f"[DEBUG] 题材关联的stock_id数量: {len(stock_ids)}")

            if not stock_ids:
                logger.warning(f"[DEBUG] 题材没有关联的股票")
                conn.close()
                return {"by_count": {}, "by_date": {}}

            # 获取最近N个交易日
            cursor.execute('''
                SELECT DISTINCT trade_date
                FROM trend_stocks
                ORDER BY trade_date DESC
                LIMIT ?
            ''', (days,))
            trading_dates = [row[0] for row in cursor.fetchall()]
            logger.info(f"[DEBUG] trading_dates: {len(trading_dates)}个")

            if not trading_dates:
                logger.warning(f"[DEBUG] 没有趋势标数据")
                conn.close()
                return {"by_count": {}, "by_date": {}}

            date_placeholders = ','.join(['?' for _ in trading_dates])
            placeholders = ','.join(['?' for _ in stock_ids])

            # 查询这些股票的趋势标数据
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
                  AND s.stock_id IN ({placeholders})
                ORDER BY ts.trade_date DESC
            '''

            cursor.execute(query, trading_dates + stock_ids)
            trend_rows = cursor.fetchall()
            logger.info(f"[DEBUG] 获取到 {len(trend_rows)} 条趋势标记录")

            import struct

            # 查询所有涨停数据
            query_limits = f'''
                SELECT
                    s.stock_code,
                    COUNT(*) as limit_count
                FROM first_limits fl
                JOIN stocks s ON fl.stock_id = s.stock_id
                WHERE fl.limit_date IN ({date_placeholders})
                  AND s.stock_id IN ({placeholders})
                GROUP BY s.stock_id
            '''

            cursor.execute(query_limits, trading_dates + stock_ids)
            limit_rows = cursor.fetchall()

            # 构建涨停次数字典
            limit_count_map = {}
            for code, count in limit_rows:
                limit_count_map[code] = count

            logger.info(f"[DEBUG] 涨停数据 {len(limit_count_map)} 只股票")

            # 查询所有新高数据
            query_highs = f'''
                SELECT
                    s.stock_code,
                    COUNT(*) as new_high_count
                FROM popularity_stocks ps
                JOIN stocks s ON ps.stock_id = s.stock_id
                WHERE ps.trade_date IN ({date_placeholders})
                  AND ps.source_id = 9
                  AND s.stock_id IN ({placeholders})
                GROUP BY s.stock_id
            '''

            cursor.execute(query_highs, trading_dates + stock_ids)
            high_rows = cursor.fetchall()

            # 构建新高次数字典
            new_high_count_map = {}
            for code, count in high_rows:
                new_high_count_map[code] = count

            logger.info(f"[DEBUG] 新高数据 {len(new_high_count_map)} 只股票")

            conn.close()

            # 按股票整理入选记录
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

            # 按入选次数分类
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

            # 按日期分类（每只股票只在最近入选日期显示）
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

            logger.info(f"[DEBUG] 完成，共{len(by_count_sorted)}个入选次数分组，{len(by_date)}个日期，{len(stock_selections)}只股票")

            return {
                "by_count": by_count_sorted,
                "by_date": by_date
            }
        except Exception as e:
            logger.error(f"获取题材趋势标失败: {e}", exc_info=True)
            return {"by_count": {}, "by_date": {}}

    def remove_topic_activation(self, topic_id: int, date: str) -> bool:
        """移除题材激活（删除某日题材卡片）

        首板板块核心逻辑（重要！）：
        - 删除topic_activations表中该日期的记录
        - 同时删除first_limit_topics表中 association_date = 该日期的所有记录

        参数说明：
        - date: 欲删除题材卡片的日期（应使用 get_query_trading_date() 返回的日期）

        全局交易日规则：
        - 9:15（开盘时间）前：上一个交易日
        - 9:15及之后：当天（如果是交易日）
        - 注意：今日首板板块不应使用 get_display_trade_date()
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 从 topic_activations 删除
            cursor.execute('''
                DELETE FROM topic_activations
                WHERE topic_id = ? AND activation_date = ?
            ''', (topic_id, date))
            affected_activations = cursor.rowcount

            # 从 first_limit_topics 删除（删除该题材在该日的所有首板关联）
            cursor.execute('''
                DELETE FROM first_limit_topics
                WHERE topic_id = ? AND association_date = ?
            ''', (topic_id, date))
            affected_first_limits = cursor.rowcount

            conn.commit()
            conn.close()

            if affected_activations > 0 or affected_first_limits > 0:
                logger.info(f"成功移除题材激活及关联: topic_id={topic_id}, date={date}")
                logger.info(f"  topic_activations: {affected_activations}条, first_limit_topics: {affected_first_limits}条")
            else:
                logger.warning(f"未找到题材激活及关联: topic_id={topic_id}, date={date}")

            return affected_activations > 0
        except Exception as e:
            logger.error(f"移除题材激活失败: {e}")
            return False

    def delete_topic(self, topic_id: int) -> bool:
        """删除整个题材及其所有关联数据"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                DELETE FROM topic_stock_relations
                WHERE topic_id = ?
            ''', (topic_id,))
            affected_relations = cursor.rowcount

            cursor.execute('''
                DELETE FROM first_limit_topics
                WHERE topic_id = ?
            ''', (topic_id,))
            affected_first_limits = cursor.rowcount

            cursor.execute('''
                DELETE FROM topic_activations
                WHERE topic_id = ?
            ''', (topic_id,))
            affected_activations = cursor.rowcount

            cursor.execute('''
                DELETE FROM rotation_actives
                WHERE topic_id = ?
            ''', (topic_id,))
            affected_rotation = cursor.rowcount

            cursor.execute('''
                DELETE FROM topics
                WHERE topic_id = ?
            ''', (topic_id,))
            affected_topics = cursor.rowcount

            conn.commit()
            conn.close()

            if affected_topics > 0:
                logger.info(f"成功删除题材: topic_id={topic_id}")
                logger.info(f"  topics: {affected_topics}条")
                logger.info(f"  topic_stock_relations: {affected_relations}条")
                logger.info(f"  first_limit_topics: {affected_first_limits}条")
                logger.info(f"  topic_activations: {affected_activations}条")
                logger.info(f"  rotation_actives: {affected_rotation}条")
            else:
                logger.warning(f"未找到题材: topic_id={topic_id}")

            return affected_topics > 0
        except Exception as e:
            logger.error(f"删除题材失败: {e}")
            return False

    def get_activated_topics(self, date: str, table: str = None) -> List[Dict]:
        """获取指定日期激活的题材列表

        参数:
        - date: 查询日期
        - table: 指定表名（None=自动判断）
                - "topic_activations" 或 "topic_activations_tmp"

        返回:
        - 题材列表
        """
        # 如果未指定表，根据时间自动判断
        if table is None:
            from src.main import is_in_trading_hours
            table = "topic_activations_tmp" if is_in_trading_hours() else "topic_activations"

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(f'''
                SELECT
                    ta.topic_id,
                    t.topic_name
                FROM {table} ta
                JOIN topics t ON ta.topic_id = t.topic_id
                WHERE ta.activation_date = ? AND ta.is_active = 1
                ORDER BY t.topic_name
            ''', (date,))

            topics = []
            for row in cursor.fetchall():
                topics.append({
                    'topic_id': row[0],
                    'topic_name': row[1]
                })

            conn.close()
            return topics
        except Exception as e:
            logger.error(f"获取激活题材列表失败: {e}")
            return []

    def get_topics_with_first_limits(self, date: str) -> List[Dict]:
        """获取指定日期激活的题材（已废弃，请使用 get_activated_topics）

        注意：
        - 此方法与 get_activated_topics 功能完全相同
        - 保留此方法仅用于向后兼容
        - 新代码应使用 get_activated_topics

        核心业务逻辑（重要！）：
        - topic_activations 表决定了今日首板板块应该展示哪些题材卡片
        - first_limit_topics 表只是记录首板和题材的关联关系
        - 如果 topic_activations 中没有激活某个题材，即使 first_limit_topics 中有关联，也不应该展示该题材

        全局交易日规则：
        - 在交易日9:15之前，所有数据显示的都应该是上一个交易日的数据
        - 参见 get_query_trading_date() 和 get_display_trade_date()
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 从 topic_activations 表获取指定日期激活的题材
            cursor.execute('''
                SELECT DISTINCT
                    ta.topic_id,
                    t.topic_name
                FROM topic_activations ta
                JOIN topics t ON ta.topic_id = t.topic_id
                WHERE ta.activation_date = ? AND ta.is_active = 1
                ORDER BY t.topic_name
            ''', (date,))

            topics = []
            for row in cursor.fetchall():
                topics.append({
                    'topic_id': row[0],
                    'topic_name': row[1]
                })

            conn.close()
            return topics
        except Exception as e:
            logger.error(f"获取激活题材列表失败: {e}")
            return []

    def get_topic_first_limits_by_association_date(
        self,
        topic_id: int,
        association_date: str,
        table: str = None
    ) -> List[Dict]:
        """获取指定题材在指定关联日期的首板标的

        首板板块核心逻辑（重要！）：
        - 使用 association_date 过滤（首板与题材的真实活跃交易日）
        - 而不是 first_limits.limit_date（首板的原始日期）

        参数说明：
        - topic_id: 题材ID
        - association_date: 关联日期（从全局displayTradeDate获取）
        - table: 指定表名（None=自动判断）
                - "first_limit_topics" 或 "first_limit_topics_tmp"

        应用场景：
        - 周日复盘周五盘面，将某个首板拖入题材
        - association_date = "2025-02-02"（周五）
        - 查询该日期的题材卡片时，使用此方法获取关联的首板
        """
        # 如果未指定表，根据时间自动判断
        if table is None:
            from src.main import is_in_trading_hours
            table = "first_limit_topics_tmp" if is_in_trading_hours() else "first_limit_topics"

        # 从题材关联表推断首板表
        first_limits_table = table.replace('_topics', 's')

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(f'''
                SELECT
                    fl.id,
                    fl.stock_id,
                    s.stock_code,
                    s.stock_name,
                    s.industry,
                    fl.limit_date,
                    fl.first_limit_time,
                    fl.final_limit_time,
                    fl.limit_price,
                    fl.limit_type,
                    fl.amount,
                    fl.reason,
                    coalesce(fl.is_exploded, 0) as is_exploded
                FROM {table} flt
                JOIN {first_limits_table} fl ON flt.first_limit_id = fl.id
                JOIN stocks s ON fl.stock_id = s.stock_id
                WHERE flt.topic_id = ? AND flt.association_date = ?
                ORDER BY fl.first_limit_time
            ''', (topic_id, association_date))

            records = []
            for row in cursor.fetchall():
                records.append({
                    'id': row[0],
                    'stock_id': row[1],
                    'code': row[2],
                    'name': row[3],
                    'sector': row[4],
                    'limit_date': row[5],
                    'first_time': row[6],
                    'final_time': row[7],
                    'price': row[8],
                    'limit_type': row[9],
                    'amount': row[10],
                    'reason': row[11],
                    'is_exploded': bool(row[12])
                })

            conn.close()
            return records
        except Exception as e:
            logger.error(f"获取题材首板标的失败: {e}")
            return []

    def get_topic_first_limits_by_date(self, topic_id: int, date: str) -> List[Dict]:
        """获取指定题材在指定日期关联的首板标的（已废弃，请使用 get_topic_first_limits_by_association_date）

        保留此方法仅用于向后兼容
        """
        return self.get_topic_first_limits_by_association_date(topic_id, date)

    def get_topic_statistics_by_days(self, topic_id: int, days: int = 24) -> Dict:
        """获取指定题材在多个交易日的历史统计数据

        参数：
        - topic_id: 题材ID
        - days: 查询的天数（默认24个交易日）

        返回：
        - dates: 日期列表（按最近的日期在前）
        - first_limits: 每日首板数量
        - continuous_limits: 每日连扳数量
        - stages: 每日状态
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 获取最近交易日期
            cursor.execute('SELECT MAX(date) FROM rotation_actives')
            result = cursor.fetchone()
            latest_date = result[0] if result and result[0] else None

            if not latest_date:
                conn.close()
                return {"dates": [], "first_limits": [], "continuous_limits": [], "stages": []}

            # 获取最近N个交易日
            cursor.execute('''
                SELECT date FROM trading_days
                WHERE date <= ? AND is_active = 1
                ORDER BY date DESC
                LIMIT ?
            ''', (latest_date, days))

            date_rows = cursor.fetchall()
            date_list = [row[0] for row in date_rows]

            # 打印查询到的日期列表（仅0209）
            if any('2025-02-09' in d or '0209' in d for d in date_list):
                logger.info(f"[DEBUG 0209] get_topic_statistics_by_days: topic_id={topic_id}, date_list={date_list}")

            first_limits = []
            continuous_limits = []
            stages = []

            for date in date_list:
                # 查询该题材在指定日期的首板数量（使用 association_date）
                cursor.execute('''
                    SELECT COUNT(DISTINCT fl.id)
                    FROM first_limit_topics flt
                    JOIN first_limits fl ON flt.first_limit_id = fl.id
                    WHERE flt.topic_id = ? AND flt.association_date = ?
                ''', (topic_id, date))
                result = cursor.fetchone()
                first_limit_count = result[0] if result and result[0] is not None else 0
                first_limits.append(first_limit_count)

                cursor.execute('''
                    SELECT COUNT(DISTINCT clh.id)
                    FROM continuous_limits_history clh
                    JOIN stocks s ON clh.code = s.stock_code
                    JOIN topic_stock_relations tsr ON s.stock_id = tsr.stock_id
                    WHERE clh.trade_date = ? AND tsr.topic_id = ? AND clh.continuous_days >= 2
                ''', (date, topic_id))
                result = cursor.fetchone()
                continuous_limit_count = result[0] if result and result[0] is not None else 0
                continuous_limits.append(continuous_limit_count)

                # 查询该题材在指定日期的状态
                cursor.execute('''
                    SELECT stage FROM rotation_actives
                    WHERE topic_id = ? AND date = ?
                ''', (topic_id, date))
                stage_result = cursor.fetchone()
                stage = stage_result[0] if stage_result and stage_result[0] else None
                stages.append(stage)

                # 打印0209这一天的查询结果
                if '2025-02-09' in date or '0209' in date:
                    logger.info(f"[DEBUG 0209] 首板: {first_limit_count}, 连板: {continuous_limit_count}, 阶段: {stage}")

            conn.close()
            return {
                "dates": date_list,
                "first_limits": first_limits,
                "continuous_limits": continuous_limits,
                "stages": stages
            }
        except Exception as e:
            logger.error(f"获取题材统计数据失败: {e}")
            return {"dates": [], "first_limits": [], "continuous_limits": [], "stages": []}

    def save_limit_stats(self, trade_date: str, first_limit: int, continuous_limit: int,
                        exploded: int, limit_down: int, explode_rate: float, market_mood: int = 3) -> int:
        """保存或更新涨跌停统计数据

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD
            first_limit: 首板数量
            continuous_limit: 连板数量
            exploded: 炸板数量
            limit_down: 跌停数量
            explode_rate: 炸板率（百分比）
            market_mood: 市场情绪（1=低迷, 2=谨慎, 3=正常, 4=活跃, 5=狂热）

        Returns:
            影响的记录数
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO limit_stats
                (trade_date, first_limit, continuous_limit, exploded, limit_down,
                 explode_rate, market_mood, create_time, update_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    first_limit = excluded.first_limit,
                    continuous_limit = excluded.continuous_limit,
                    exploded = excluded.exploded,
                    limit_down = excluded.limit_down,
                    explode_rate = excluded.explode_rate,
                    market_mood = excluded.market_mood,
                    update_time = excluded.update_time
            ''', (trade_date, first_limit, continuous_limit, exploded, limit_down,
                  explode_rate, market_mood, now, now))

            affected = cursor.rowcount
            conn.commit()
            conn.close()

            logger.info(f"保存涨跌停统计数据: {trade_date}, 首板={first_limit}, 连板={continuous_limit}")
            return affected
        except Exception as e:
            logger.error(f"保存涨跌停统计数据失败: {e}", exc_info=True)
            return 0

    def get_limit_stats_by_date(self, trade_date: str) -> Optional[Dict]:
        """获取指定交易日的涨跌停统计数据

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD

        Returns:
            涨跌停统计数据字典，如果不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT trade_date, first_limit, continuous_limit, exploded,
                       limit_down, explode_rate, market_mood, create_time, update_time
                FROM limit_stats
                WHERE trade_date = ?
            ''', (trade_date,))

            row = cursor.fetchone()
            conn.close()

            if row:
                from data.database import MARKET_MOOD_MAP
                return {
                    'trade_date': row[0],
                    'first_limit': row[1],
                    'continuous_limit': row[2],
                    'exploded': row[3],
                    'limit_down': row[4],
                    'explode_rate': row[5],
                    'market_mood': row[6],
                    'market_mood_text': MARKET_MOOD_MAP.get(row[6], '未知'),
                    'create_time': row[7],
                    'update_time': row[8]
                }
            return None
        except Exception as e:
            logger.error(f"获取涨跌停统计数据失败: {e}")
            return None

    def get_latest_limit_stats(self) -> Optional[Dict]:
        """获取最新交易日的涨跌停统计数据

        Returns:
            最新交易日的涨跌停统计数据字典，如果不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT trade_date, first_limit, continuous_limit, exploded,
                       limit_down, explode_rate, market_mood, create_time, update_time
                FROM limit_stats
                ORDER BY trade_date DESC
                LIMIT 1
            ''', ())

            row = cursor.fetchone()
            conn.close()

            if row:
                from data.database import MARKET_MOOD_MAP
                return {
                    'trade_date': row[0],
                    'first_limit': row[1],
                    'continuous_limit': row[2],
                    'exploded': row[3],
                    'limit_down': row[4],
                    'explode_rate': row[5],
                    'market_mood': row[6],
                    'market_mood_text': MARKET_MOOD_MAP.get(row[6], '未知'),
                    'create_time': row[7],
                    'update_time': row[8]
                }
            return None
        except Exception as e:
            logger.error(f"获取最新涨跌停统计数据失败: {e}")
            return None

    def save_limit_analysis(self, trade_date: str, analysis: str) -> int:
        """保存或更新涨跌停分析说明

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD
            analysis: 分析说明内容

        Returns:
            影响的记录数
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO limit_stats_analysis (trade_date, analysis, create_time, update_time)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    analysis = excluded.analysis,
                    update_time = excluded.update_time
            ''', (trade_date, analysis, now, now))

            affected = cursor.rowcount
            conn.commit()
            conn.close()

            logger.info(f"保存涨跌停分析: {trade_date}")
            return affected
        except Exception as e:
            logger.error(f"保存涨跌停分析失败: {e}")
            return 0

    def get_limit_analysis(self, trade_date: str) -> Optional[Dict]:
        """获取指定交易日的涨跌停分析说明

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD

        Returns:
            分析说明字典，如果不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT trade_date, analysis, create_time, update_time
                FROM limit_stats_analysis
                WHERE trade_date = ?
            ''', (trade_date,))

            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    'trade_date': row[0],
                    'analysis': row[1],
                    'create_time': row[2],
                    'update_time': row[3]
                }
            return None
        except Exception as e:
            logger.error(f"获取涨跌停分析失败: {e}")
            return None

    def save_market_status_summary(self, trade_date: str, summary_content: str) -> int:
        """保存或更新市场整体状态总结

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD
            summary_content: 总结内容

        Returns:
            影响的记录数
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO market_status_summary (trade_date, summary_content, create_time, update_time)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    summary_content = excluded.summary_content,
                    update_time = excluded.update_time
            ''', (trade_date, summary_content, now, now))

            affected = cursor.rowcount
            conn.commit()
            conn.close()

            logger.info(f"保存市场整体状态总结: {trade_date}")
            return affected
        except Exception as e:
            logger.error(f"保存市场整体状态总结失败: {e}")
            return 0

    def get_market_status_summary(self, trade_date: str) -> Optional[Dict]:
        """获取指定交易日的市场整体状态总结

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD

        Returns:
            总结内容字典，如果不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT trade_date, summary_content, create_time, update_time
                FROM market_status_summary
                WHERE trade_date = ?
            ''', (trade_date,))

            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    'trade_date': row[0],
                    'summary_content': row[1],
                    'create_time': row[2],
                    'update_time': row[3]
                }
            return None
        except Exception as e:
            logger.error(f"获取市场整体状态总结失败: {e}")
            return None

    def save_continuous_limits_analysis(self, trade_date: str, analysis: str) -> int:
        """保存或更新连板梯队分析说明

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD
            analysis: 分析说明内容

        Returns:
            影响的记录数
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO continuous_limits_analysis (trade_date, analysis, create_time, update_time)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    analysis = excluded.analysis,
                    update_time = excluded.update_time
            ''', (trade_date, analysis, now, now))

            affected = cursor.rowcount
            conn.commit()
            conn.close()

            logger.info(f"保存连板梯队分析: {trade_date}")
            return affected
        except Exception as e:
            logger.error(f"保存连板梯队分析失败: {e}")
            return 0

    def get_continuous_limits_analysis(self, trade_date: str) -> Optional[Dict]:
        """获取指定交易日的连板梯队分析说明

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD

        Returns:
            分析说明字典，如果不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT trade_date, analysis, create_time, update_time
                FROM continuous_limits_analysis
                WHERE trade_date = ?
            ''', (trade_date,))

            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    'trade_date': row[0],
                    'analysis': row[1],
                    'create_time': row[2],
                    'update_time': row[3]
                }
            return None
        except Exception as e:
            logger.error(f"获取连板梯队分析失败: {e}")
            return None

    def save_continuous_limits_history(self, trade_date: str, limits: List[Dict]) -> int:
        """保存指定交易日的连板梯队历史数据

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD
            limits: 连板股票列表，每个股票包含：
                - code: 股票代码
                - name: 股票名称
                - price: 股票价格
                - first_time: 首次涨停时间（格式：HH:MM）
                - continuous_days: 连续涨停天数
                - sector: 所属题材
                - reason: 涨停原因
                - amount: 成交额（亿元）

        Returns:
            保存的记录数
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()
            saved_count = 0

            for limit in limits:
                try:
                    cursor.execute('''
                        INSERT OR REPLACE INTO continuous_limits_history
                        (trade_date, code, name, price, first_time, continuous_days, sector, reason, amount, create_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        trade_date,
                        limit.get('code', ''),
                        limit.get('name', ''),
                        limit.get('price'),
                        limit.get('first_time', ''),
                        limit.get('continuous_days', 0),
                        limit.get('sector', ''),
                        limit.get('reason', ''),
                        limit.get('amount'),
                        now
                    ))
                    saved_count += 1
                except Exception as e:
                    logger.warning(f"保存连板历史数据失败: {limit}, 错误: {e}")

            conn.commit()
            conn.close()

            logger.info(f"保存连板梯队历史数据: {trade_date}, 共 {saved_count} 条")
            return saved_count
        except Exception as e:
            logger.error(f"保存连板梯队历史数据失败: {e}")
            return 0

    def get_continuous_limits_by_date(self, trade_date: str) -> List[Dict]:
        """查询指定交易日的连板梯队历史数据

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD

        Returns:
            连板股票列表
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT trade_date, code, name, price, first_time, 
                       continuous_days, sector, reason, amount
                FROM continuous_limits_history
                WHERE trade_date = ?
                ORDER BY continuous_days DESC, first_time ASC
            ''', (trade_date,))

            records = []
            for row in cursor.fetchall():
                records.append({
                    'trade_date': row[0],
                    'code': row[1],
                    'name': row[2],
                    'price': row[3],
                    'first_time': row[4],
                    'continuous_days': row[5],
                    'sector': row[6] or '综合',
                    'reason': row[7] or '持续强势',
                    'amount': row[8]
                })

            conn.close()
            return records
        except Exception as e:
            logger.error(f"查询连板梯队历史数据失败: {e}")
            return []

    def get_previous_trading_day(self, date: str) -> Optional[str]:
        """获取指定日期的上一个交易日

        Args:
            date: 指定日期（格式：YYYY-MM-DD）

        Returns:
            上一个交易日的日期字符串（格式：YYYY-MM-DD），如果不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT date
                FROM trading_days
                WHERE date < ? AND is_active = 1
                ORDER BY date DESC
                LIMIT 1
            ''', (date,))

            row = cursor.fetchone()
            conn.close()

            if row:
                return row[0]
            return None
        except Exception as e:
            logger.error(f"获取上一交易日失败: {e}")
            return None

    def get_limit_stats_history(self, limit: int = 30, date: str = None) -> List[Dict]:
        """获取涨跌停统计数据

        Args:
            limit: 获取天数，默认30天
            date: 截止日期（包含），如果提供则只返回该日期之前的数据

        Returns:
            涨跌停统计数据列表
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if date:
                # 只返回指定日期之前（包含）的数据
                cursor.execute('''
                    SELECT trade_date, first_limit, continuous_limit, exploded,
                           limit_down, explode_rate, market_mood
                    FROM limit_stats
                    WHERE trade_date <= ?
                    ORDER BY trade_date DESC
                    LIMIT ?
                ''', (date, limit))
            else:
                # 获取最近N天的数据
                cursor.execute('''
                    SELECT trade_date, first_limit, continuous_limit, exploded,
                           limit_down, explode_rate, market_mood
                    FROM limit_stats
                    ORDER BY trade_date DESC
                    LIMIT ?
                ''', (limit,))

            records = []
            for row in cursor.fetchall():
                records.append({
                    'date': row[0],
                    'first_limit': row[1],
                    'continuous_limit': row[2],
                    'exploded': row[3],
                    'limit_down': row[4],
                    'explode_rate': row[5]
                })

            conn.close()
            return records
        except Exception as e:
            logger.error(f"获取涨跌停历史数据失败: {e}")
            return []

    def calculate_limit_stats_median(self, field: str, days: int = 10) -> float:
        """计算指定字段最近N天的中位数

        Args:
            field: 字段名（first_limit, continuous_limit, exploded, limit_down）
            days: 天数，默认10天

        Returns:
            中位数值
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(f'''
                SELECT {field} FROM limit_stats
                ORDER BY trade_date DESC
                LIMIT ?
            ''', (days,))

            values = [row[0] for row in cursor.fetchall()]
            conn.close()

            if not values:
                return 0.0

            values.sort()
            length = len(values)

            if length % 2 == 0:
                median = (values[length // 2 - 1] + values[length // 2]) / 2
            else:
                median = float(values[length // 2])

            return median
        except Exception as e:
            logger.error(f"计算中位数失败: {e}")
            return 0.0

    def get_previous_limit_stats(self, trade_date: str) -> Optional[Dict]:
        """获取指定交易日上一个交易日的涨跌停统计数据

        Args:
            trade_date: 交易日期，格式：YYYY-MM-DD

        Returns:
            上一交易日的涨跌停统计数据字典，如果不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT trade_date, first_limit, continuous_limit, exploded,
                       limit_down, explode_rate, market_mood
                FROM limit_stats
                WHERE trade_date < ?
                ORDER BY trade_date DESC
                LIMIT 1
            ''', (trade_date,))

            row = cursor.fetchone()
            conn.close()

            if row:
                from data.database import MARKET_MOOD_MAP
                return {
                    'trade_date': row[0],
                    'first_limit': row[1],
                    'continuous_limit': row[2],
                    'exploded': row[3],
                    'limit_down': row[4],
                    'explode_rate': row[5],
                    'market_mood': row[6],
                    'market_mood_text': MARKET_MOOD_MAP.get(row[6], '未知')
                }
            return None
        except Exception as e:
            logger.error(f"获取上一交易日涨跌停统计数据失败: {e}")
            return None

    def get_all_trading_days_before_today(self) -> List[str]:
        """获取今天之前的所有交易日

        今日首板板块核心逻辑（重要！）：
        - 用于日期选择器，只显示今天及以前的交易日
        - 过滤掉未来日期（trading_days 表可能包含未来的交易日）
        - 按日期降序返回（最新的日期在前）

        Returns:
            交易日列表（格式：YYYY-MM-DD），按时间倒序排列（从晚到早）
        """
        try:
            today = datetime.now().strftime("%Y-%m-%d")

            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT date
                FROM trading_days
                WHERE date <= ? AND is_active = 1
                ORDER BY date DESC
            ''', (today,))

            days = [row[0] for row in cursor.fetchall()]
            conn.close()

            return days
        except Exception as e:
            logger.error(f"获取今日之前的交易日列表失败: {e}")
            return []

    def get_trading_days_backwards_from_date(self, date: str, count: int = 20) -> List[str]:
        """从指定日期开始往前获取N个交易日

        Args:
            date: 指定日期（格式：YYYY-MM-DD），该日期必须是交易日
            count: 获取的交易日数量，默认30天

        Returns:
            交易日列表（格式：YYYY-MM-DD），按时间正序排列（从早到晚）
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 查询小于等于指定日期的N个交易日，按日期降序
            cursor.execute('''
                SELECT date
                FROM trading_days
                WHERE date <= ? AND is_active = 1
                ORDER BY date DESC
                LIMIT ?
            ''', (date, count))

            rows = cursor.fetchall()
            conn.close()

            # 反转列表，使其按时间正序排列（从早到晚）
            return [row[0] for row in rows][::-1]
        except Exception as e:
            logger.error(f"获取交易日列表失败: {e}")
            return []

    # 【已注释】人气榜相关数据库操作函数
    def get_popularity_sources(self) -> List[Dict]:
        """获取所有人气榜数据源（选项卡）- 包含"人气、热度"相关数据源
        关联关系：
        - 被 main.py 的 get_stock_popularity_data() 和 API endpoints 调用
        - 操作的表：popularity_sources, popularity_stocks
        - 前端通过 /api/hot-stocks 获取 popularity_sources 数据
        过滤说明：这里返回所有数据源，前端会过滤出"新高榜"（半年新高、一年新高、历史新高）
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT source_id, source_name, description, sort_order, is_active, created_at, updated_at
                FROM popularity_sources
                ORDER BY sort_order ASC, source_id ASC
            ''')

            records = []
            for row in cursor.fetchall():
                records.append({
                    'source_id': row[0],
                    'source_name': row[1],
                    'description': row[2],
                    'sort_order': row[3],
                    'is_active': row[4],
                    'created_at': row[5],
                    'updated_at': row[6]
                })

            conn.close()
            return records
        except Exception as e:
            logger.error(f"获取人气榜数据源失败: {e}")
            return []

    def create_popularity_source(self, source_name: str, description: str = '', sort_order: int = 0) -> Optional[int]:
        """创建人气榜数据源（选项卡）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO popularity_sources (source_name, description, sort_order, is_active, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
            ''', (source_name, description, sort_order, now, now))

            source_id = cursor.lastrowid
            conn.commit()
            conn.close()

            logger.info(f"创建人气榜数据源成功: {source_name} (ID: {source_id})")
            return source_id
        except Exception as e:
            logger.error(f"创建人气榜数据源失败: {e}")
            return None

    def update_popularity_source(self, source_id: int, source_name: str = None, description: str = None, sort_order: int = None, is_active: int = None) -> bool:
        """更新人气榜数据源"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()

            updates = []
            params = []

            if source_name is not None:
                updates.append('source_name = ?')
                params.append(source_name)
            if description is not None:
                updates.append('description = ?')
                params.append(description)
            if sort_order is not None:
                updates.append('sort_order = ?')
                params.append(sort_order)
            if is_active is not None:
                updates.append('is_active = ?')
                params.append(is_active)

            updates.append('updated_at = ?')
            params.append(now)
            params.append(source_id)

            if updates:
                cursor.execute(f'''
                    UPDATE popularity_sources
                    SET {', '.join(updates)}
                    WHERE source_id = ?
                ''', params)

                conn.commit()
                conn.close()
                logger.info(f"更新人气榜数据源成功: source_id={source_id}")
                return True

            conn.close()
            return False
        except Exception as e:
            logger.error(f"更新人气榜数据源失败: {e}")
            return False

    def delete_popularity_source(self, source_id: int) -> bool:
        """删除人气榜数据源（级联删除其下的所有标的记录）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('DELETE FROM popularity_sources WHERE source_id = ?', (source_id,))

            conn.commit()
            conn.close()

            logger.info(f"删除人气榜数据源成功: source_id={source_id}")
            return True
        except Exception as e:
            logger.error(f"删除人气榜数据源失败: {e}")
            return False

    def get_popularity_stocks(self, source_id: int, trade_date: str) -> List[Dict]:
        """获取指定数据源和交易日的人气榜标的

        关联关系：
        - 被 main.py 的 get_stock_popularity_data() 调用
        - 操作 popularity_stocks 表
        - 前端通过 /api/hot-stocks 获取人气榜标的列表
        过滤说明：本函数根据source_id获取数据，source_id在前端过滤后传入

        Args:
            source_id: 数据源ID
            trade_date: 交易日期（格式：YYYY-MM-DD）

        Returns:
            人气榜标的列表
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT ps.rank, s.stock_code, s.stock_name, ps.price, ps.change_percent, ps.amount, ps.sector, ps.reason, s.industry, ps.created_at, ps.updated_at
                FROM popularity_stocks ps
                JOIN stocks s ON ps.stock_id = s.stock_id
                WHERE ps.source_id = ? AND ps.trade_date = ?
                ORDER BY ps.rank ASC
            ''', (source_id, trade_date))

            records = []
            for row in cursor.fetchall():
                records.append({
                    'rank': row[0],
                    'code': row[1],
                    'name': row[2],
                    'price': row[3],
                    'change_percent': row[4],
                    'amount': row[5],
                    'sector': row[6] or row[8] or '综合',
                    'reason': row[7] or '',
                    'industry': row[8] or '综合',
                    'created_at': row[9],
                    'updated_at': row[10]
                })

            conn.close()
            return records
        except Exception as e:
            logger.error(f"查询人气榜标的失败: {e}")
            return []

    def save_popularity_stocks(self, source_id: int, trade_date: str, stocks: List[Dict]) -> int:
        """保存人气榜标的数据（同一交易日同一排名只能有一个标的）

        关联关系：
        - 被 main.py 的 save_popularity_stocks API endpoint 调用
        - 被 data_acquisition.py fetch_and_save_popularity_ranking() 调用
        - 操作 popularity_stocks 表
        过滤说明：保存"新高榜"数据源的数据

        Args:
            source_id: 数据源ID
            trade_date: 交易日期（格式：YYYY-MM-DD）
            stocks: 股票列表，每个股票包含：
                - code: 股票代码
                - name: 股票名称
                - price: 股票价格
                - change_percent: 涨跌幅
                - amount: 成交额（亿元）
                - sector: 所属题材
                - reason: 榜单原因

        Returns:
            保存的记录数
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()
            saved_count = 0

            for stock in stocks:
                try:
                    code = stock.get('code', '')
                    name = stock.get('name', '')

                    if not code or not name:
                        continue

                    cursor.execute('''
                        INSERT OR REPLACE INTO stocks (stock_code, stock_name, created_at, updated_at)
                        VALUES (?, ?, ?, ?)
                    ''', (code, name, now, now))

                    cursor.execute('SELECT stock_id FROM stocks WHERE stock_code = ?', (code,))
                    stock_id = cursor.fetchone()[0]

                    rank = stock.get('rank', 0)

                    cursor.execute('''
                        INSERT OR REPLACE INTO popularity_stocks
                        (source_id, stock_id, trade_date, rank, price, change_percent, amount, sector, reason, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        source_id,
                        stock_id,
                        trade_date,
                        rank,
                        stock.get('price'),
                        stock.get('change_percent'),
                        stock.get('amount'),
                        stock.get('sector', '综合'),
                        stock.get('reason', ''),
                        now,
                        now
                    ))

                    saved_count += 1
                except Exception as e:
                    logger.warning(f"保存人气榜标的失败: {stock}, 错误: {e}")

            conn.commit()
            conn.close()

            logger.info(f"保存人气榜标的: source_id={source_id}, trade_date={trade_date}, 共 {saved_count} 条")
            return saved_count
        except Exception as e:
            logger.error(f"保存人气榜标的失败: {e}")
            return 0

    def get_strong_stock_types(self) -> List[Dict]:
        """获取强势股热度类型列表

        关联关系：
        - 被main.py的get_strong_stocks API endpoint调用
        - 返回strong_stock_types表数据
        过滤说明：获取所有活跃的强势股热度类型

        Returns:
            热度类型列表，每个类型包含：
            - type_id: 类型ID
            - type_name: 类型名称
            - description: 描述
            - sort_order: 排序
            - is_active: 是否活跃
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT type_id, type_name, description, sort_order, is_active
                FROM strong_stock_types
                WHERE is_active = 1
                ORDER BY sort_order, type_id
            ''')

            types = []
            for row in cursor.fetchall():
                types.append({
                    'type_id': row[0],
                    'type_name': row[1],
                    'description': row[2],
                    'sort_order': row[3],
                    'is_active': row[4]
                })

            conn.close()
            return types

        except Exception as e:
            logger.error(f"获取强势股热度类型失败: {e}")
            return []

    def get_strong_stocks(self, hot_type: str = None, trade_date: str = None) -> List[Dict]:
        """获取强势股数据

        关联关系：
        - 被main.py的get_strong_stocks API endpoint调用
        - 查询strong_stocks表
        过滤说明：
        - 如果指定hot_type，返回该热度类型的强势股
        - 如果不指定hot_type，返回所有强势股

        Args:
            hot_type: 热度类型（如'60日新高'），不传则返回所有类型
            trade_date: 交易日期（格式：YYYY-MM-DD），不传则返回最新

        Returns:
            强势股列表，每个股票包含：
            - id: 记录ID
            - stock_id: 股票ID
            - code: 股票代码
            - name: 股票名称
            - trade_date: 交易日期
            - hot_type: 热度类型
            - rank: 排名
            - price: 股票价格
            - change_percent: 涨跌幅
            - amount: 成交额
            - turnover_rate: 换手率
            - volume_ratio: 量比
            - is_new_high: 是否新高
            - continuous_limit_days: 连续涨停天数
            - sector: 行业
            - reason: 原因
            - topics: 题材列表(最近异动的题材)
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if hot_type:
                cursor.execute('''
                    SELECT ss.id, ss.stock_id, s.stock_code, s.stock_name, s.industry,
                           ss.trade_date, ss.hot_type, ss.rank, ss.price, ss.change_percent,
                           ss.amount, ss.turnover_rate, ss.volume_ratio, ss.is_new_high,
                           ss.continuous_limit_days, ss.sector, ss.reason
                    FROM strong_stocks ss
                    JOIN stocks s ON ss.stock_id = s.stock_id
                    WHERE ss.hot_type = ?
                    AND ss.trade_date = ?
                    ORDER BY ss.rank
                ''', (hot_type, trade_date))
            else:
                cursor.execute('''
                    SELECT ss.id, ss.stock_id, s.stock_code, s.stock_name, s.industry,
                           ss.trade_date, ss.hot_type, ss.rank, ss.price, ss.change_percent,
                           ss.amount, ss.turnover_rate, ss.volume_ratio, ss.is_new_high,
                           ss.continuous_limit_days, ss.sector, ss.reason
                    FROM strong_stocks ss
                    JOIN stocks s ON ss.stock_id = s.stock_id
                    WHERE ss.trade_date = ?
                    ORDER BY ss.hot_type, ss.rank
                ''', (trade_date,))

            stocks = []
            for row in cursor.fetchall():
                stock_id = row[1]
                stocks.append({
                    'id': row[0],
                    'stock_id': stock_id,
                    'code': row[2],
                    'name': row[3],
                    'industry': row[4],
                    'trade_date': row[5],
                    'hot_type': row[6],
                    'rank': row[7],
                    'price': row[8],
                    'change_percent': row[9],
                    'amount': row[10],
                    'turnover_rate': row[11],
                    'volume_ratio': row[12],
                    'is_new_high': row[13],
                    'continuous_limit_days': row[14],
                    'sector': row[15],
                    'reason': row[16],
                    'topics': []
                })

            conn = self._get_connection()
            cursor = conn.cursor()

            for stock in stocks:
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
            return stocks

        except Exception as e:
            logger.error(f"获取强势股失败: {e}")
            return []

    def create_strong_stock_type(self, type_name: str, description: str = '', sort_order: int = 0) -> int:
        """创建强势股热度类型

        Args:
            type_name: 类型名称
            description: 描述
            sort_order: 排序

        Returns:
            type_id: 类型ID，失败返回-1
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO strong_stock_types
                (type_name, description, sort_order, is_active, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
            ''', (type_name, description, sort_order, now, now))

            type_id = cursor.lastrowid
            conn.commit()
            conn.close()

            logger.info(f"创建强势股热度类型: type_id={type_id}, type_name={type_name}")
            return type_id

        except Exception as e:
            logger.error(f"创建强势股热度类型失败: {e}")
            return -1

    def get_amount_types(self) -> List[Dict]:
        """获取所有成交额榜类型（选项卡）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT type_id, type_name, description, query_time, is_active, created_at, updated_at
                FROM amount_types
                ORDER BY type_id ASC
            ''')

            records = []
            for row in cursor.fetchall():
                records.append({
                    'type_id': row[0],
                    'type_name': row[1],
                    'description': row[2],
                    'query_time': row[3],
                    'is_active': row[4],
                    'created_at': row[5],
                    'updated_at': row[6]
                })

            conn.close()
            return records
        except Exception as e:
            logger.error(f"获取成交额榜类型失败: {e}")
            return []

    def get_amount_stocks(self, type_id: int, trade_date: str) -> List[Dict]:
        """获取指定类型和交易日的成交额榜标的

        Args:
            type_id: 类型ID（竞价成交额或全天成交额）
            trade_date: 交易日期（格式：YYYY-MM-DD）

        Returns:
            成交额榜标的列表
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT am.rank, s.stock_code, s.stock_name, am.price, am.change_percent, am.amount, am.sector, am.reason, am.is_final, am.created_at, am.updated_at
                FROM amount_stocks am
                JOIN stocks s ON am.stock_id = s.stock_id
                WHERE am.type_id = ? AND am.trade_date = ?
                ORDER BY am.rank ASC
            ''', (type_id, trade_date))

            records = []
            for row in cursor.fetchall():
                records.append({
                    'rank': row[0],
                    'code': row[1],
                    'name': row[2],
                    'price': row[3],
                    'change_percent': row[4],
                    'amount': row[5],
                    'sector': row[6] or '综合',
                    'reason': row[7] or '',
                    'is_final': row[8],
                    'created_at': row[9],
                    'updated_at': row[10]
                })

            conn.close()
            return records
        except Exception as e:
            logger.error(f"查询成交额榜标的失败: {e}")
            return []

    def get_amount_type_by_name(self, type_name: str) -> Optional[Dict]:
        """根据类型名称获取成交额榜类型

        Args:
            type_name: 类型名称（如："竞价成交额"、"全天成交额"）

        Returns:
            类型字典，如果不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                SELECT type_id, type_name, description, query_time, is_active, created_at, updated_at
                FROM amount_types
                WHERE type_name = ?
            ''', (type_name,))

            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    'type_id': row[0],
                    'type_name': row[1],
                    'description': row[2],
                    'query_time': row[3],
                    'is_active': row[4],
                    'created_at': row[5],
                    'updated_at': row[6]
                }
            return None
        except Exception as e:
            logger.error(f"根据名称查询成交额榜类型失败: {e}")
            return None

    def save_amount_stocks(self, type_id: int, trade_date: str, stocks: List[Dict], check_final: bool = False) -> int:
        """保存成交额榜标的数据

        Args:
            type_id: 类型ID
            trade_date: 交易日期（格式：YYYY-MM-DD）
            stocks: 股票列表
            check_final: 是否检查是否已标记为final（不可修改）

        Returns:
            保存的记录数，如果已标记为final则返回0
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if check_final:
                cursor.execute('''
                    SELECT COUNT(*)
                    FROM amount_stocks
                    WHERE type_id = ? AND trade_date = ? AND is_final = 1
                ''', (type_id, trade_date))

                if cursor.fetchone()[0] > 0:
                    conn.close()
                    logger.warning(f"成交额榜数据已标记为final，不可修改: type_id={type_id}, trade_date={trade_date}")
                    return 0

            now = datetime.now().isoformat()
            saved_count = 0

            for stock in stocks:
                try:
                    code = stock.get('code', '')
                    name = stock.get('name', '')

                    if not code or not name:
                        continue

                    cursor.execute('''
                        INSERT OR REPLACE INTO stocks (stock_code, stock_name, created_at, updated_at)
                        VALUES (?, ?, ?, ?)
                    ''', (code, name, now, now))

                    cursor.execute('SELECT stock_id FROM stocks WHERE stock_code = ?', (code,))
                    stock_id = cursor.fetchone()[0]

                    rank = stock.get('rank', 0)

                    cursor.execute('''
                        INSERT OR REPLACE INTO amount_stocks
                        (type_id, stock_id, trade_date, rank, price, change_percent, amount, sector, reason, is_final, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    ''', (
                        type_id,
                        stock_id,
                        trade_date,
                        rank,
                        stock.get('price'),
                        stock.get('change_percent'),
                        stock.get('amount'),
                        stock.get('sector', '综合'),
                        stock.get('reason', ''),
                        now,
                        now
                    ))

                    saved_count += 1
                except Exception as e:
                    logger.warning(f"保存成交额榜标的失败: {stock}, 错误: {e}")

            conn.commit()
            conn.close()

            logger.info(f"保存成交额榜标的: type_id={type_id}, trade_date={trade_date}, 共 {saved_count} 条")
            return saved_count
        except Exception as e:
            logger.error(f"保存成交额榜标的失败: {e}")
            return 0

    def set_amount_stocks_final(self, type_id: int, trade_date: str) -> bool:
        """将成交额榜数据标记为final（不可修改）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.now().isoformat()

            cursor.execute('''
                UPDATE amount_stocks
                SET is_final = 1, updated_at = ?
                WHERE type_id = ? AND trade_date = ?
            ''', (now, type_id, trade_date))

            conn.commit()
            conn.close()

            logger.info(f"将成交额榜标记为final: type_id={type_id}, trade_date={trade_date}")
            return True
        except Exception as e:
            logger.error(f"标记成交额榜为final失败: {e}")
            return False

    def clear_first_limits_tmp_tables(self) -> None:
        """清空所有临时表数据（盘中数据）

        清空内容：
        - first_limits_tmp（首板临时数据）
        - first_limit_topics_tmp（题材关联临时数据）
        - topic_activations_tmp（题材激活临时数据）

        业务场景：
        - 盘中：每次刷新前清空（准备全量覆盖）
        - 盘后/非交易日：清空临时表（临时数据视为错误数据）
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 清空首板临时表
            cursor.execute('DELETE FROM first_limits_tmp')
            deleted_limits = cursor.rowcount

            # 清空题材关联临时表
            cursor.execute('DELETE FROM first_limit_topics_tmp')
            deleted_relations = cursor.rowcount

            # 清空题材激活临时表
            cursor.execute('DELETE FROM topic_activations_tmp')
            deleted_activations = cursor.rowcount

            conn.commit()
            conn.close()

            logger.info(f"清空临时表完成: "
                       f"首板{deleted_limits}条, 关联{deleted_relations}条, 激活{deleted_activations}条")

        except Exception as e:
            logger.error(f"清空临时表失败: {e}", exc_info=True)

    def save_first_limits_to_specific_table(self, data_list, table: str, date: str) -> int:
        """保存首板数据到指定表（临时表或正式表）

        参数:
        - data_list: 首板数据列表
        - table: 目标表名（"first_limits" 或 "first_limits_tmp"）
        - date: 查询日期

        返回:
        - 保存的记录数

        业务场景：
        - 盘中：保存到 first_limits_tmp
        - 盘后/非交易日：保存到 first_limits
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            saved_count = 0

            for stock in data_list:
                # 获取或创建股票记录（注意：使用外部连接，不支持 con/cursor 参数）
                stock_id = self.get_or_create_stock(
                    stock.get('code', ''),
                    stock.get('name', ''),
                    stock.get('sector', '')
                )

                if stock_id == -1:
                    continue

                # 插入首板记录（使用 INSERT OR REPLACE 实现覆盖）
                cursor.execute(f'''
                    INSERT OR REPLACE INTO {table}
                    (stock_id, limit_date, first_limit_time, limit_price,
                     open_price, amount, limit_type, reason, source, create_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    stock_id,
                    date,
                    stock.get('first_time', ''),
                    stock.get('price', 0),
                    stock.get('open_price', 0),
                    stock.get('amount', 0),
                    stock.get('limit_type', '10%'),
                    stock.get('reason', ''),
                    stock.get('source', 'akshare'),
                    datetime.now().isoformat()
                ))

                saved_count += 1

            conn.commit()
            conn.close()

            logger.info(f"保存首板数据到 {table}: {saved_count} 条")
            return saved_count

        except Exception as e:
            logger.error(f"保存首板数据失败: {e}", exc_info=True)
            return 0

    def check_first_limits_date_exists(self, date: str, table: str = "first_limits") -> bool:
        """检查指定日期在首板表中是否有数据

        参数:
        - date: 查询日期
        - table: 指定表名（默认正式表）

        返回:
        - True=有数据，False=无数据
        
        业务场景：
        - 盘外刷新前检查正式表是否已有数据
        - 如果有数据，不自动创建题材（保护用户手动操作）
        - 如果没有数据，自动创建题材（初始化）
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(f'''
                SELECT COUNT(*)
                FROM {table}
                WHERE limit_date = ?
            ''', (date,))

            count = cursor.fetchone()[0]

            conn.close()

            has_data = count > 0

            logger.info(f"检查 {table} 日期 {date} 是否有数据: {has_data} ({count} 条)")

            return has_data

        except Exception as e:
            logger.error(f"检查首板数据是否存在失败: {e}")
            return False

    def check_topic_activations_date_exists(self, date: str, table: str = "topic_activations") -> bool:
        """检查指定日期在题材激活表中是否有数据

        参数:
        - date: 查询日期
        - table: 指定表名（默认正式表）

        返回:
        - True=有题材卡片，False=无题材卡片
        
        业务场景：
        - 盘外刷新前检查正式表是否已有题材卡片
        - 如果有题材卡片，不自动创建（保护用户手动操作，如删除的题材）
        - 如果没有题材卡片，自动创建（初始化）
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(f'''
                SELECT COUNT(*)
                FROM {table}
                WHERE activation_date = ?
            ''', (date,))

            count = cursor.fetchone()[0]

            conn.close()

            has_activations = count > 0

            logger.info(f"检查 {table} 日期 {date} 是否有题材卡片: {has_activations} ({count} 个)")

            return has_activations

        except Exception as e:
            logger.error(f"检查题材激活是否存在失败: {e}")
            return False

    def auto_create_topic_cards_for_date(
        self,
        date: str,
        topics_table: str,
        activations_table: str,
        first_limits_table: str = None
    ) -> List[Dict]:
        """自动创建题材卡片并归类首板标的

        流程:
        1. 查询指定日期的所有首板（从 first_limits_table）
        2. 查询每个首板股票在 topic_stock_relations 中的题材关联
        3. 对于每个题材：
           - 激活题材卡片（写入 activations_table）
           - 关联首板到题材（写入 topics_table）
        4. 清理无效题材（没有首板关联的）

        参数:
        - date: 查询日期
        - topics_table: 题材关联表名（first_limit_topics 或 first_limit_topics_tmp）
        - activations_table: 题材激活表名（topic_activations 或 topic_activations_tmp）
        - first_limits_table: 首板表名（可选，默认与 topics_table 对应）

        返回:
        - 激活的题材列表 [{'topic_id': int, 'topic_name': str}]
        """
        try:
            # 如果未指定首板表，从题材表推断
            if first_limits_table is None:
                first_limits_table = topics_table.replace('_topics', 's')

            conn = self._get_connection()
            cursor = conn.cursor()

            # 步骤1: 查询指定日期的首板
            cursor.execute(f'''
                SELECT DISTINCT stock_id
                FROM {first_limits_table}
                WHERE limit_date = ?
            ''', (date,))

            stock_ids = [row[0] for row in cursor.fetchall()]

            if not stock_ids:
                logger.info(f"日期 {date} 没有首板数据，无需创建题材卡片")
                return []

            logger.info(f"[自动创建题材] 开始为 {len(stock_ids)} 只首板创建题材卡片...")

            # 步骤2: 查询每个股票的题材关联（从 topic_stock_relations）
            placeholders = ','.join(['?' for _ in stock_ids])
            cursor.execute(f'''
                SELECT DISTINCT
                    tsr.topic_id,
                    t.topic_name
                FROM topic_stock_relations tsr
                JOIN topics t ON tsr.topic_id = t.topic_id
                WHERE tsr.stock_id IN ({placeholders})
                ORDER BY t.topic_name
            ''', stock_ids)

            topic_rows = cursor.fetchall()
            topic_map = {row[0]: row[1] for row in topic_rows}
            topic_ids = list(topic_map.keys())

            logger.info(f"[自动创建题材] 发现 {len(topic_ids)} 个题材需要处理")

            if not topic_ids:
                logger.info(f"[自动创建题材] 没有发现题材关联")
                return []

            # 步骤3: 激活题材卡片
            activated_topics = []
            for topic_id in topic_ids:
                topic_name = topic_map[topic_id]

                # 检查是否已激活
                cursor.execute(f'''
                    SELECT id FROM {activations_table}
                    WHERE topic_id = ? AND activation_date = ?
                ''', (topic_id, date))

                if not cursor.fetchone():
                    # 激活题材
                    now = datetime.now().isoformat()
                    cursor.execute(f'''
                        INSERT OR IGNORE INTO {activations_table}
                        (topic_id, activation_date, is_active, created_at, updated_at)
                        VALUES (?, ?, 1, ?, ?)
                    ''', (topic_id, date, now, now))

                    activated_topics.append({
                        'topic_id': topic_id,
                        'topic_name': topic_name
                    })
                    logger.info(f"[自动创建题材] 激活题材: {topic_name}")

            # 步骤4: 关联首板到题材
            # 先查询哪些股票-题材组合需要关联
            stock_placeholders = ','.join(['?' for _ in stock_ids])
            topic_placeholders = ','.join(['?' for _ in topic_ids])
            
            cursor.execute(f'''
                SELECT DISTINCT
                    tsr.stock_id,
                    tsr.topic_id
                FROM topic_stock_relations tsr
                WHERE tsr.stock_id IN ({stock_placeholders})
                  AND tsr.topic_id IN ({topic_placeholders})
            ''', stock_ids + topic_ids)

            stock_topic_pairs = cursor.fetchall()
            关联_count = 0

            for stock_id, topic_id in stock_topic_pairs:
                # 检查该股票在指定日期是否是首板
                cursor.execute(f'''
                    SELECT id FROM {first_limits_table}
                    WHERE stock_id = ? AND limit_date = ?
                ''', (stock_id, date))

                limit_record = cursor.fetchone()

                if limit_record:
                    first_limit_id = limit_record[0]

                    # 插入到题材关联表
                    cursor.execute(f'''
                        INSERT OR IGNORE INTO {topics_table}
                        (stock_id, first_limit_id, topic_id, create_time, association_date)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (
                        stock_id,
                        first_limit_id,
                        topic_id,
                        datetime.now().isoformat(),
                        date
                    ))
                    关联_count += 1

            # 步骤5: 清理无效题材（没有首板关联的）
            cursor.execute(f'''
                SELECT DISTINCT ta.topic_id, t.topic_name
                FROM {activations_table} ta
                JOIN topics t ON ta.topic_id = t.topic_id
                WHERE ta.activation_date = ?
                AND ta.topic_id NOT IN (
                    SELECT DISTINCT topic_id FROM {topics_table}
                    WHERE association_date = ?
                )
            ''', (date, date))

            invalid_topics = cursor.fetchall()

            for topic_id, topic_name in invalid_topics:
                cursor.execute(f'''
                    DELETE FROM {activations_table}
                    WHERE topic_id = ? AND activation_date = ?
                ''', (topic_id, date))
                logger.info(f"[自动创建题材] 删除无效题材: {topic_name}")

            conn.commit()
            conn.close()

            logger.info(f"[自动创建题材] 完成: "
                       f"激活{len(activated_topics)}个, 关联{关联_count}个, 删除{len(invalid_topics)}个")

            return activated_topics

        except Exception as e:
            logger.error(f"自动创建题材卡片失败: {e}", exc_info=True)
            return []
