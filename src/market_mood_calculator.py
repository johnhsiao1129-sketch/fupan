"""
市场情绪计算器
用于根据多维度指标计算市场情绪积分，判断市场处于哪个阶段
"""
import logging
import sqlite3
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class MarketMoodCalculator:
    """市场情绪计算器"""
    
    def __init__(self, db_path: str):
        """
        初始化计算器
        
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        self.conn = None
    
    def _get_connection(self):
        """获取数据库连接"""
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
        return self.conn
    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def calculate_market_mood(self, trade_date: str) -> Dict:
        """
        计算指定交易日的市场情绪
        
        Args:
            trade_date: 交易日期，格式 YYYY-MM-DD
            
        Returns:
            {
                'success': bool,
                'trade_date': str,
                'total_score': float,
                'mood_level': int,
                'mood_name': str,
                'indicator_scores': dict,  # 各指标最终得分
                'indicator_details': dict,  # 各指标详细数据
                'normalized_scores': dict, # 各指标归一化得分
                'message': str
            }
        """
        try:
            logger.info(f"开始计算市场情绪：日期={trade_date}")
            
            # 获取数据库连接
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # ========== 第一步：获取基础数据 ==========
            logger.info("=== 第一步：获取基础数据 ===")
            base_data = self._get_base_data(cursor, trade_date)
            if not base_data:
                return {
                    'success': False,
                    'message': f'无法获取 {trade_date} 的基础数据'
                }
            
            # ========== 第二步：获取历史中位数 ==========
            logger.info("=== 第二步：获取历史中位数 ===")
            medians = self._get_medians(cursor, trade_date)
            
            # ========== 第三步：获取启用的配置规则 ==========
            logger.info("=== 第三步：获取配置规则 ===")
            configs = self._get_enabled_configs(cursor)
            logger.info(f"获取到 {len(configs)} 个启用的指标配置")
            
            # ========== 第四步：计算各指标得分 ==========
            logger.info("=== 第四步：计算各指标得分 ===")
            indicator_scores = {}
            normalized_scores = {}
            indicator_details = {}
            
            total_score = 0.0
            
            for config in configs:
                indicator_code = config['indicator_code']
                category = config['category']
                direction = config['direction']
                weight = config['weight']
                baseline = config.get('baseline')
                
                # 根据类别计算
                if category == 'basic':
                    score, normalized, detail = self._calculate_basic_indicator(
                        indicator_code, base_data, medians, weight, baseline, direction
                    )
                elif category == 'continuity':
                    score, normalized, detail = self._calculate_continuity_indicator(
                        indicator_code, base_data, weight, direction
                    )
                elif category == 'topic':
                    score, normalized, detail = self._calculate_topic_indicator(
                        indicator_code, base_data, weight, direction
                    )
                elif category == 'integration':
                    score, normalized, detail = self._calculate_integration_indicator(
                        indicator_code, base_data, weight, direction
                    )
                else:
                    logger.warning(f"未知类别: {category}")
                    continue
                
                # 累计总分
                total_score += score
                
                # 保存结果
                indicator_scores[indicator_code] = score
                if normalized is not None:
                    normalized_scores[indicator_code] = normalized
                if detail is not None:
                    indicator_details[indicator_code] = detail
                
                norm_str = f"{normalized:.3f}" if normalized is not None else "None"
                logger.info(f"指标 {indicator_code}: normalized={norm_str}, weight={weight}, score={score:.3f}")
            
            # ========== 第五步：根据总分确定情绪等级 ==========
            logger.info("=== 第五步：确定情绪等级 ===")
            mood = self._get_mood_by_score(cursor, total_score)
            
            # ========== 第六步：保存计算结果 ==========
            logger.info("=== 第六步：保存计算结果 ===")
            self._save_calculation_result(
                cursor, trade_date, total_score, mood,
                indicator_scores, normalized_scores, indicator_details
            )
            
            conn.commit()
            
            logger.info(f"市场情绪计算完成：total_score={total_score:.2f}, mood={mood['mood_name']}")
            
            return {
                'success': True,
                'trade_date': trade_date,
                'total_score': round(total_score, 2),
                'mood_level': mood['mood_level'],
                'mood_name': mood['mood_name'],
                'indicator_scores': indicator_scores,
                'indicator_details': indicator_details,
                'normalized_scores': normalized_scores,
                'message': f'市场情绪计算成功'
            }
            
        except Exception as e:
            logger.error(f"计算市场情绪失败: {e}", exc_info=True)
            return {
                'success': False,
                'message': f'计算失败: {str(e)}'
            }
    
    def _get_base_data(self, cursor: sqlite3.Cursor, trade_date: str) -> Optional[Dict]:
        """
        获取基础数据
        
        Args:
            cursor: 数据库游标
            trade_date: 交易日期
            
        Returns:
            基础数据字典，如果失败返回None
        """
        try:
            # 获取涨跌停统计数据
            cursor.execute('''
                SELECT first_limit, continuous_limit, exploded, explode_rate, limit_down
                FROM limit_stats
                WHERE trade_date = ?
            ''', (trade_date,))
            limit_stats_row = cursor.fetchone()
            
            if not limit_stats_row:
                logger.warning(f"未找到 {trade_date} 的涨跌停统计")
                return None
            
            data = {
                'first_limit': limit_stats_row[0] or 0,
                'continuous_limit': limit_stats_row[1] or 0,
                'exploded': limit_stats_row[2] or 0,
                'explode_rate': limit_stats_row[3] or 0.0,
                'limit_down': limit_stats_row[4] or 0,
            }
            
            # 获取新高数据（从 popularity_stocks 表）
            cursor.execute('''
                SELECT 
                    (SELECT COUNT(*) FROM popularity_stocks WHERE source_id = 9 AND trade_date = ?) as all_time_high,
                    (SELECT COUNT(*) FROM popularity_stocks WHERE source_id = 8 AND trade_date = ?) as one_year_high,
                    (SELECT COUNT(*) FROM popularity_stocks WHERE source_id = 7 AND trade_date = ?) as six_month_high
            ''', (trade_date, trade_date, trade_date))
            
            highs_row = cursor.fetchone()
            
            if highs_row:
                data['all_time_high'] = highs_row[0] or 0
                data['one_year_high'] = highs_row[1] or 0
                data['six_month_high'] = highs_row[2] or 0
            else:
                data['all_time_high'] = 0
                data['one_year_high'] = 0
                data['six_month_high'] = 0
            
            # 获取连板梯队数据（从 continuous_limits_history 表按 continuous_days 统计）
            cursor.execute('''
                SELECT continuous_days as tier_height, COUNT(*) as stock_count
                FROM continuous_limits_history
                WHERE trade_date = ? AND continuous_days >= 2
                GROUP BY continuous_days
            ''', (trade_date,))
            continuous_rows = cursor.fetchall()

            data['continuous_tiers'] = {row[0]: row[1] for row in continuous_rows}
            
            # 获取题材数据
            cursor.execute('''
                SELECT topic_name, topic_stage, continuity_days
                FROM topic_continuity
                WHERE trade_date = ?
            ''', (trade_date,))
            topic_rows = cursor.fetchall()
            
            data['topics'] = [{'name': row[0], 'stage': row[1], 'days': row[2]} for row in topic_rows]
            
            # 获取题材首板数据（从topic_stock_relations表）
            cursor.execute('''
                SELECT t.topic_name, COUNT(*) as count
                FROM topic_stock_relations tsr
                JOIN topics t ON tsr.topic_id = t.topic_id
                WHERE tsr.date = ? AND tsr.relation_type = 'first_limit'
                GROUP BY t.topic_name
            ''', (trade_date,))
            topic_first_limits = {row[0]: row[1] for row in cursor.fetchall()}
            data['topic_first_limits'] = topic_first_limits
            
            # 获取连续跌停数据（从 limit_down_history 表统计）
            cursor.execute('''
                SELECT 
                    SUM(CASE WHEN continuous_days = 2 THEN 1 ELSE 0 END) as count_2day,
                    SUM(CASE WHEN continuous_days = 3 THEN 1 ELSE 0 END) as count_3day,
                    SUM(CASE WHEN continuous_days = 4 THEN 1 ELSE 0 END) as count_4day,
                    SUM(CASE WHEN continuous_days >= 5 THEN 1 ELSE 0 END) as count_5day_plus
                FROM limit_down_history
                WHERE trade_date = ?
            ''', (trade_date,))
            lows_row = cursor.fetchone()
            
            if lows_row:
                data['continuous_down_2day'] = lows_row[0] or 0
                data['continuous_down_3day'] = lows_row[1] or 0
                data['continuous_down_4day'] = lows_row[2] or 0
                data['continuous_down_5day_plus'] = lows_row[3] or 0
            else:
                data['continuous_down_2day'] = 0
                data['continuous_down_3day'] = 0
                data['continuous_down_4day'] = 0
                data['continuous_down_5day_plus'] = 0
            
            # 获取指数数据
            cursor.execute('''
                SELECT change_percent
                FROM market_index
                WHERE trade_date = ?
            ''', (trade_date,))
            index_row = cursor.fetchone()
            
            if index_row:
                data['index_change'] = index_row[0] or 0.0
            else:
                data['index_change'] = 0.0
            
            return data
            
        except Exception as e:
            logger.error(f"获取基础数据失败: {e}", exc_info=True)
            return None
    
    def _get_medians(self, cursor: sqlite3.Cursor, trade_date: str) -> Dict:
        """
        获取历史中位数（归一化基准）
        
        Args:
            cursor: 数据库游标
            trade_date: 当前交易日期
            
        Returns:
            各指标的历史中位数字典
        """
        medians = {}
        
        try:
            # 方法：查询当前日期之前的最近30条数据，计算中位数
            # 这样可以获取更准确的中位数，而不是基于时间范围的估算
            
            # 首板中位数（使用最近10个交易日，包含当天；限定 trade_date <= 计算日，确保历史日期重算时 medians 不变）
            cursor.execute('''
                SELECT trade_date, first_limit
                FROM limit_stats
                WHERE trade_date <= ?
                ORDER BY trade_date DESC LIMIT 10
            ''', (trade_date,))
            first_limits = [row[1] for row in cursor.fetchall()]
            if first_limits:
                first_limits_sorted = sorted(first_limits)
                if len(first_limits_sorted) % 2 == 0:
                    medians['first_limit'] = (first_limits_sorted[len(first_limits_sorted)//2 - 1] + first_limits_sorted[len(first_limits_sorted)//2]) / 2
                else:
                    medians['first_limit'] = first_limits_sorted[len(first_limits_sorted)//2]
            else:
                medians['first_limit'] = 30  # 默认值

            # 连板中位数（使用最近10个交易日，包含当天；限定 trade_date <= 计算日）
            cursor.execute('''
                SELECT trade_date, continuous_limit
                FROM limit_stats
                WHERE trade_date <= ?
                ORDER BY trade_date DESC LIMIT 10
            ''', (trade_date,))
            continuous_limits = [row[1] for row in cursor.fetchall()]
            if continuous_limits:
                continuous_limits_sorted = sorted(continuous_limits)
                if len(continuous_limits_sorted) % 2 == 0:
                    medians['continuous_limit'] = (continuous_limits_sorted[len(continuous_limits_sorted)//2 - 1] + continuous_limits_sorted[len(continuous_limits_sorted)//2]) / 2
                else:
                    medians['continuous_limit'] = continuous_limits_sorted[len(continuous_limits_sorted)//2]
            else:
                medians['continuous_limit'] = 10.5  # 默认值

            # 跌停中位数（使用最近10个交易日，包含当天；限定 trade_date <= 计算日）
            cursor.execute('''
                SELECT trade_date, limit_down
                FROM limit_stats
                WHERE trade_date <= ?
                ORDER BY trade_date DESC LIMIT 10
            ''', (trade_date,))
            limit_downs = [row[1] for row in cursor.fetchall()]
            if limit_downs:
                limit_downs_sorted = sorted(limit_downs)
                if len(limit_downs_sorted) % 2 == 0:
                    medians['limit_down'] = (limit_downs_sorted[len(limit_downs_sorted)//2 - 1] + limit_downs_sorted[len(limit_downs_sorted)//2]) / 2
                else:
                    medians['limit_down'] = limit_downs_sorted[len(limit_downs_sorted)//2]
            else:
                medians['limit_down'] = 5  # 默认值
            
            # 获取新高数据（从 popularity_stocks 表）
            # 分别计算三种新高的中位数
            
            # 历史新高中位数
            cursor.execute('''
                SELECT trade_date, COUNT(*) as count
                FROM popularity_stocks
                WHERE source_id = 9 AND trade_date < ?
                GROUP BY trade_date
                ORDER BY trade_date DESC LIMIT 10
            ''', (trade_date,))
            all_time_counts = [row[1] for row in cursor.fetchall()]
            if all_time_counts:
                all_time_sorted = sorted(all_time_counts)
                if len(all_time_sorted) % 2 == 0:
                    medians['all_time_high'] = (all_time_sorted[len(all_time_sorted)//2 - 1] + all_time_sorted[len(all_time_sorted)//2]) / 2
                else:
                    medians['all_time_high'] = all_time_sorted[len(all_time_sorted)//2]
            else:
                medians['all_time_high'] = None
            
            # 一年新高中位数
            cursor.execute('''
                SELECT trade_date, COUNT(*) as count
                FROM popularity_stocks
                WHERE source_id = 8 AND trade_date < ?
                GROUP BY trade_date
                ORDER BY trade_date DESC LIMIT 10
            ''', (trade_date,))
            one_year_counts = [row[1] for row in cursor.fetchall()]
            if one_year_counts:
                one_year_sorted = sorted(one_year_counts)
                if len(one_year_sorted) % 2 == 0:
                    medians['one_year_high'] = (one_year_sorted[len(one_year_sorted)//2 - 1] + one_year_sorted[len(one_year_sorted)//2]) / 2
                else:
                    medians['one_year_high'] = one_year_sorted[len(one_year_sorted)//2]
            else:
                medians['one_year_high'] = None
            
            # 半年新高中位数
            cursor.execute('''
                SELECT trade_date, COUNT(*) as count
                FROM popularity_stocks
                WHERE source_id = 7 AND trade_date < ?
                GROUP BY trade_date
                ORDER BY trade_date DESC LIMIT 10
            ''', (trade_date,))
            six_month_counts = [row[1] for row in cursor.fetchall()]
            if six_month_counts:
                six_month_sorted = sorted(six_month_counts)
                if len(six_month_sorted) % 2 == 0:
                    medians['six_month_high'] = (six_month_sorted[len(six_month_sorted)//2 - 1] + six_month_sorted[len(six_month_sorted)//2]) / 2
                else:
                    medians['six_month_high'] = six_month_sorted[len(six_month_sorted)//2]
            else:
                medians['six_month_high'] = None
            
            # 炸板率中位数（使用最近10个交易日，包含当天；限定 trade_date <= 计算日）
            cursor.execute('''
                SELECT explode_rate
                FROM limit_stats
                WHERE trade_date <= ?
                ORDER BY trade_date DESC LIMIT 10
            ''', (trade_date,))
            explode_rates = [row[0] for row in cursor.fetchall()]
            if explode_rates:
                explode_rates_sorted = sorted(explode_rates)
                if len(explode_rates_sorted) % 2 == 0:
                    medians['explode_rate'] = (explode_rates_sorted[len(explode_rates_sorted)//2 - 1] + explode_rates_sorted[len(explode_rates_sorted)//2]) / 2
                else:
                    medians['explode_rate'] = explode_rates_sorted[len(explode_rates_sorted)//2]
            else:
                medians['explode_rate'] = 15  # 默认值
            
            logger.info(f"历史中位数: {medians}")
            return medians
            
        except Exception as e:
            logger.error(f"获取历史中位数失败: {e}", exc_info=True)
            # 返回默认值
            return {
                'first_limit': 30,
                'continuous_limit': 10.5,
                'limit_down': 5,
                'all_time_high': 20,
                'one_year_high': None,
                'six_month_high': None
            }
    
    def _get_enabled_configs(self, cursor: sqlite3.Cursor) -> List[Dict]:
        """
        获取启用的指标配置
        
        Args:
            cursor: 数据库游标
            
        Returns:
            配置列表
        """
        try:
            cursor.execute('''
                SELECT indicator_code, category, indicator_name, direction, weight, baseline
                FROM market_mood_config
                WHERE is_enabled = 1
                ORDER BY category, indicator_code
            ''')
            
            configs = []
            for row in cursor.fetchall():
                configs.append({
                    'indicator_code': row[0],
                    'category': row[1],
                    'indicator_name': row[2],
                    'direction': row[3],
                    'weight': row[4],
                    'baseline': row[5]
                })
            
            return configs
            
        except Exception as e:
            logger.error(f"获取配置失败: {e}", exc_info=True)
            return []
    
    def _calculate_basic_indicator(
        self, 
        indicator_code: str, 
        data: Dict, 
        medians: Dict, 
        weight: float, 
        baseline: Optional[float],
        direction: str
    ) -> Tuple[float, Optional[float], Optional[Dict]]:
        """
        计算基础类指标
        
        Args:
            indicator_code: 指标代码
            data: 基础数据
            medians: 历史中位数
            weight: 权重
            baseline: 基准值
            direction: 方向（positive/negative/mixed）
            
        Returns:
            (最终得分, 归一化得分, 详细信息)
        """
        normalized = None
        detail = None
        
        # ========== 归一化通用公式 ==========
        # 对于相对于中位数的指标：
        # normalized = (value - median) / (median * 0.5)
        # 最终结果限制在 [-1, 1] 范围内
        
        # 首板 vs 中位数
        if indicator_code == 'first_limit_vs_median':
            median = medians.get('first_limit', 30)
            value = data.get('first_limit', 0)
            
            if median > 0:
                normalized = (value - median) / (median * 0.5)
                # 限制在 [-1, 1]
                normalized = max(-1, min(1, normalized))
            else:
                normalized = 0
            
            score = normalized * weight
            detail = {
                'value': value,
                'median': median,
                'diff': value - median,
                'ratio': value / median if median > 0 else 0
            }
        
        # 连板 vs 中位数
        elif indicator_code == 'continuous_vs_median':
            median = medians.get('continuous_limit', 15)
            value = data.get('continuous_limit', 0)
            
            if median > 0:
                normalized = (value - median) / (median * 0.5)
                normalized = max(-1, min(1, normalized))
            else:
                normalized = 0
            
            score = normalized * weight
            detail = {
                'value': value,
                'median': median,
                'diff': value - median,
                'ratio': value / median if median > 0 else 0
            }
        
        # 历史新高：只使用all_time_high，数据唯一不重复
        elif indicator_code == 'all_time_high':
            value = data.get('all_time_high', 0)
            median = medians.get('all_time_high', 0)
            
            if median is None or median == 0:
                return 0, None, {'reason': '没有新高数据，不参与计算'}
            
            if median > 0:
                normalized = (value - median) / (median * 0.5)
                normalized = max(-1, min(1, normalized))
            else:
                normalized = 0
            
            score = normalized * weight
            detail = {
                'value': value,
                'median': median,
                'diff': value - median
            }
        
        # 一年新高和半年新高：不参与计算（数据有重复）
        elif indicator_code in ['one_year_high', 'six_month_high']:
            return 0, None, {'reason': '此指标已禁用，数据重复，只使用历史新高指标'}
        
        # 炸板率（负面指标）
        elif indicator_code == 'explode_rate':
            # 炸板率是负面指标，越高越不好
            # 归一化：超出基准的部分为负归一化
            median_value = medians.get('explode_rate', 15)
            value = data.get('explode_rate', 0)

            if median_value > 0:
                # 归一化：(中位数 - 炸板率) / (中位数 * 0.5)
                # 炸板率高于中位数为负归一化
                normalized = (median_value - value) / (median_value * 0.5)
                # 限制在 [-1, 1]
                normalized = max(-1, min(1, normalized))
            else:
                normalized = 0

            # 正权重 * 归一化值（炸板率高于中位时为负）= 负分
            score = normalized * weight
            detail = {
                'value': value,
                'median_5days': median_value,
                'diff': value - median_value
            }
        
        # 跌停 vs 中位数
        elif indicator_code == 'limit_down_vs_median':
            median = medians.get('limit_down', 5)
            value = data.get('limit_down', 0)
            
            if median > 0:
                normalized = (value - median) / (median * 0.5)
                normalized = max(-1, min(1, normalized))
            else:
                normalized = 0
            
            score = normalized * weight
            detail = {
                'value': value,
                'median': median,
                'diff': value - median,
                'ratio': value / median if median > 0 else 0
            }
        
        elif indicator_code == 'limit_down_base':
            value = data.get('limit_down', 0)
            m = 1.5
            p = 1.2

            x = len(str(value))
            first_num = int(value / 10 ** (x - 1))

            if value <= 5:
                kf = 0
            else:
                if x == 1:
                    kf = m ** x
                else:
                    kf = first_num * p * m ** (x - 1) * (10 ** (x - 2))

            score = -kf
            detail = {
                'value': value,
                'score': score,
                'first_num': first_num,
                'digits': x
            }

            return score, None, detail

        # ========== 跌停权重修正试用 ==========
        # 跌停额外扣分（第二扣分项）：结合绝对值和倍数的数学公式
        # 公式：penalty = (|v-m|^1.2×0.35 + (v/m-1)^2×3.0×0.65) × (m+1.5)/(m+7.5) × weight × 0.2
        # 特点：无if-else判断，单行公式解决；中下减少扣分，中上保持重伤门槛
        elif indicator_code == 'limit_down_extra':
            value = data.get('limit_down', 0)
            median = medians.get('limit_down', 5)

            if value <= median:
                return 0, 0, {
                    'value': value,
                    'median': median,
                    'reason': '低于或等于中位数，不额外扣分'
                }

            if median <= 0:
                penalty = value * 0.5 * weight
                return -penalty, 0, {
                    'value': value,
                    'median': median,
                    'score': -penalty,
                    'reason': '中位数为0，按绝对值扣分'
                }

            abs_diff = value - median
            ratio = value / median

            # 核心公式：绝对值项 + 倍数项 × 基数调整 × 缩放系数
            penalty = (abs_diff ** 1.2 * 0.35 + (ratio - 1) ** 2 * 3.0 * 0.65) * (median + 1.5) / (median + 7.5) * weight * 0.2

            score = -penalty
            detail = {
                'value': value,
                'median': median,
                'abs_diff': abs_diff,
                'ratio': ratio,
                'base_factor': (median + 1.5) / (median + 7.5),
                'penalty': penalty,
                'score': score
            }

            return score, 0, detail

        else:
            score = 0
            detail = {'error': f'未知指标: {indicator_code}'}

        return score, normalized, detail

    def _get_punishment_level(self, value: int) -> str:
        """获取跌停惩罚等级"""
        if value <= 5:
            return '正常'
        elif value <= 10:
            return '偏多'
        elif value <= 20:
            return '情绪较差'
        elif value <= 40:
            return '很差'
        else:
            return '市场垮塌'

    def _calculate_continuity_indicator(
        self,
        indicator_code: str,
        data: Dict,
        weight: float,
        direction: str
    ) -> Tuple[float, Optional[float], Optional[Dict]]:
        """
        计算连板梯队类指标
        
        Args:
            indicator_code: 指标代码
            data: 基础数据
            weight: 权重
            direction: 方向
            
        Returns:
            (最终得分, 归一化得分, 详细信息)
        """
        tiers = data.get('continuous_tiers', {})
        score = 0
        detail = None

        # 梯队完整性（方案A改进版：基础0分，断层扣分）
        if indicator_code == 'ladder_completeness':
            # 梯队完整性规则：
            # - 基础分为0（不断层就是0分）
            # - 找出"有效最高板数"：
            #   * 从实际最高板向下检查，如果遇到断层(断层点及以下不算)或8板(妖股不看)
            #   * 最高8板时，检查2~7连扳是否有断层
            #   * 最高9板及以上但没有8板时，不用考虑7断板（妖股不参与检验范围）
            #   * 例如：最高13板，下面是124 -> 有效最高板为4板，考虑23是否断层
            #   * 例如：最高8板，下面是12456 -> 有效检验范围8, 37断板
            #   * 例如：最高7板，下面是12346 -> 有效最高板为7板，5断板
            # - 断层扣分：断2板扣分最多(大量扣分)，断3板快速减少，越高扣分越少
            #   缺2板-15分，缺3板-8分，缺4板-5分，缺5板-3分，缺6板-2分，缺7板-1分

            # 位置扣分系数（断2板扣分最多，反映市场毫无延续性）
            deduct_scores = {
                2: -15,
                3: -8,
                4: -5,
                5: -3,
                6: -2,
                7: -1
            }

            # 基础分为0
            base_score = 0

            # 找出实际最高板数
            if tiers:
                max_height = max(tiers.keys())
            else:
                max_height = 0

            # 如果连2板都没有，给最低分
            if max_height < 2:
                score = -20
                detail = {
                    'base_score': base_score,
                    'total_deduct': -20,
                    'final_score': -20,
                    'max_height': max_height,
                    'effective_max': 0,
                    'missing_tiers': [],
                    'reason': '无2板及以上'
                }
            else:
                # 确定有效检验范围上限
                # 从实际最高板向下检查，遇到8板就停（妖股），遇到断层就停（断层点不算）
                effective_max = max_height

                for height in range(max_height, 2, -1):
                    if height >= 8:
                        # 8板及以上（妖股），有效检验范围上限为7
                        effective_max = 7
                        break
                    if height not in tiers or tiers[height] == 0:
                        # 遇到第一个断层，有效检验范围上限为该断层之前一层
                        effective_max = height - 1
                        break

                # 如果有效最高板小于2，说明没有完整的梯队结构
                if effective_max < 2:
                    score = -20
                    detail = {
                        'base_score': base_score,
                        'total_deduct': -20,
                        'final_score': -20,
                        'max_height': max_height,
                        'effective_max': effective_max,
                        'missing_tiers': [],
                        'reason': f'有效最高板为{effective_max}板，无完整梯队'
                    }
                else:
                    # 检查从2板到effective_max板之间是否有断层
                    # 只检查2~7板，因为8板及以上是妖股不考虑
                    total_deduct = 0
                    missing_tiers = []

                    for height in range(2, min(effective_max, 7) + 1):
                        if height not in tiers or tiers[height] == 0:
                            # 该层缺失，扣分
                            deduct_score = deduct_scores.get(height, 0)
                            total_deduct += deduct_score
                            missing_tiers.append(f'{height}板')

                    # 最终得分 = 0 + 扣分
                    score = base_score + total_deduct

                    detail = {
                        'base_score': base_score,
                        'total_deduct': total_deduct,
                        'final_score': score,
                        'max_height': max_height,
                        'effective_max': effective_max,
                        'missing_tiers': missing_tiers,
                        'reason': '完整' if total_deduct == 0 else f'缺{len(missing_tiers)}层'
                    }

        # 5板及以上
        elif indicator_code == 'height_5plus':
            count = tiers.get(5, 0)
            # 每只5板积2分
            score = count * 2
            detail = {'count': count, 'score_per_stock': 2}
        
        # 6板及以上
        elif indicator_code == 'height_6plus':
            count = tiers.get(6, 0)
            # 每只是6板积2.5分
            score = count * 2.5
            detail = {'count': count, 'score_per_stock': 2.5}
        
        # 7板及以上
        elif indicator_code == 'height_7plus':
            count = tiers.get(7, 0)
            # 每只是7板积3分
            score = count * 3
            detail = {'count': count, 'score_per_stock': 3}
        
        # 梯队饱满度（改进版：等于标准为0分，有数据但不达标扣分，超过加分）
        elif indicator_code == 'ladder_fullness':
            # 梯队饱满度规则：
            # - 只检查实际存在的层级
            # - 各层标准数量（等于为0分）：2板7个, 3板5个, 4板3个, 5板2个, 6板1个, 7板1个
            # - 超过标准：加分
            # - 少于标准：扣分
            # - 该层级没有数据：不扣分（视为正常范围）

            # 标准数量
            standard_counts = {
                2: 7,
                3: 5,
                4: 3,
                5: 2,
                6: 1,
                7: 1
            }

            # 找出实际最高板数
            if tiers:
                max_height = max(tiers.keys())
            else:
                max_height = 0

            # 如果连2板都没有，无法评估饱满度
            if max_height < 2:
                score = 0
                detail = {
                    'max_height': max_height,
                    'score': 0,
                    'reason': '无2板及以上'
                }
            else:
                # 计算每层权重
                tier_weights = {}
                base_weight = 0.4
                for h in range(2, min(max_height + 1, 8)):
                    tier_weights[h] = base_weight
                    base_weight *= 0.7

                total_tier_weight = sum(tier_weights.values())
                tier_weights = {h: w / total_tier_weight for h, w in tier_weights.items()}

                total_score = 0
                tier_details = {}

                for height in range(2, max_height + 1):
                    # 检查该层级是否在tiers中存在
                    if height not in tiers:
                        # 该层级不存在数据，不扣分
                        tier_details[height] = {
                            'count': 0,
                            'standard': standard_counts.get(height, 1),
                            'tier_weight': tier_weights.get(height, 0.1),
                            'tier_max_score': weight * tier_weights.get(height, 0.1),
                            'diff_ratio': 0,
                            'score': 0,
                            'note': '无数据'
                        }
                        continue

                    count = tiers[height]
                    standard = standard_counts.get(height, 1)
                    tier_weight = tier_weights.get(height, 0.1)
                    tier_max_score = weight * tier_weight

                    # 等于标准为0分
                    if standard > 0:
                        # 使用大于判断，相等为0分
                        diff_ratio = 0
                        if count > standard:
                            # 超过标准，加分
                            diff_ratio = (count - standard) / standard * 0.5  # 超过部分按50%权重
                            diff_ratio = min(1, diff_ratio)  # 最多加满
                        elif count < standard:
                            # 低于标准，扣分
                            diff_ratio = (count - standard) / standard  # 全部扣分
                        # count == standard 时 diff_ratio = 0
                    else:
                        diff_ratio = 0

                    # 该层得分
                    tier_score = tier_max_score * diff_ratio

                    total_score += tier_score

                    tier_details[height] = {
                        'count': count,
                        'standard': standard,
                        'tier_weight': tier_weight,
                        'tier_max_score': tier_max_score,
                        'diff_ratio': diff_ratio,
                        'score': tier_score
                    }

                score = total_score

                detail = {
                    'max_height': max_height,
                    'total_score': total_score,
                    'tiers': tier_details
                }

        
        # 最高层突破
        elif indicator_code == 'top_tier_breakthrough':
            # 最高层突破：检查今日最高连板是否突破近7日纪录
            # 获取近7日最高连板
            # 暂时简化处理：如果7板及以上数量>0，算突破
            
            max_height = max(tiers.keys()) if tiers else 0
            
            if max_height >= 5:
                score = 6  # 突破
                detail = {'max_height': max_height, 'breakthrough': True}
            else:
                score = 0  # 未突破
                detail = {'max_height': max_height, 'breakthrough': False}
        
        return score, None, detail
    
    def _calculate_topic_indicator(
        self,
        indicator_code: str,
        data: Dict,
        weight: float,
        direction: str
    ) -> Tuple[float, Optional[float], Optional[Dict]]:
        """
        计算题材延续性类指标
        
        Args:
            indicator_code: 指标代码
            data: 基础数据
            weight: 权重
            direction: 方向
            
        Returns:
            (最终得分, 归一化得分, 详细信息)
        """
        topics = data.get('topics', [])
        score = 0
        normalized = None
        detail = None
        
        # 题材活跃度（状态化）
        if indicator_code == 'topic_activity':
            # 题材活跃度计算：
            # - 每个题材根据其状态、连续天数和首板数量计算活跃分
            # - 状态：启动/爆发=+0.5分/天，维持=0分，分歧/退潮=-0.3分/天，回流=+0.3分/天
            # - 首板数量贡献：每只首板+0.1分
            # - 连续性：断档≤5天算连续
            
            activity_score = 0
            topic_details = []
            topic_first_limits = data.get('topic_first_limits', {})
            
            for topic in topics:
                topic_name = topic.get('name')
                stage = topic.get('stage')
                days = topic.get('days', 1)
                first_limit_count = topic_first_limits.get(topic_name, 0)
                
                # 计算连续性（简化处理，实际应该检查历史连续性）
                # 暂时假设都是连续的
                continuity_days = days
                
                # 根据状态确定方向和系数
                if stage in ['startup', 'explosion']:
                    multiplier = +0.5
                elif stage in ['divergence', 'recede']:
                    multiplier = -0.3
                elif stage == 'backflow':
                    multiplier = +0.3
                else:  # maintain
                    multiplier = 0
                
                # 计算该题材的活跃分 = 连续天数 * 状态系数 + 首板数量 * 0.1
                topic_score = (continuity_days * multiplier) + (first_limit_count * 0.1)
                activity_score += topic_score
                
                topic_details.append({
                    'name': topic_name,
                    'stage': stage,
                    'days': continuity_days,
                    'first_limit_count': first_limit_count,
                    'multiplier': multiplier,
                    'score': topic_score
                })
            
            score = activity_score
            detail = {
                'total': activity_score,
                'topics': topic_details
            }
        
        # 热点题材数量
        elif indicator_code == 'hot_topics_count':
            # 热点题材定义：首板≥5只
            topic_first_limits = data.get('topic_first_limits', {})
            hot_count = sum(
                1 for count in topic_first_limits.values() 
                if count >= 5
            )
            
            # 每个热点题材积1.5分
            score = hot_count * 1.5
            detail = {
                'count': hot_count,
                'score_per_topic': 1.5,
                'hot_topics': [name for name, count in topic_first_limits.items() if count >= 5]
            }
        
        # 单题材首板强度
        elif indicator_code == 'topic_strength':
            # 找出首板数最多的题材
            topic_first_limits = data.get('topic_first_limits', {})
            max_strength = max(topic_first_limits.values()) if topic_first_limits else 0
            
            # 找到最强题材的名称
            strongest_topic = max(topic_first_limits, key=topic_first_limits.get) if topic_first_limits else None
            
            # 归一化：最高强度题材的首板数 / 20（20只首板算满分）
            normalized = min(max_strength / 20, 1)
            score = normalized * weight
            detail = {
                'strongest_topic': strongest_topic,
                'max_strength_count': max_strength,
                'normalized': normalized
            }
        
        # 返回结果：topic_strength有归一化得分，其他指标没有
        normalized_score = normalized if indicator_code == 'topic_strength' else None
        return score, normalized_score, detail
    
    def _calculate_integration_indicator(
        self,
        indicator_code: str,
        data: Dict,
        weight: float,
        direction: str
    ) -> Tuple[float, Optional[float], Optional[Dict]]:
        """
        计算综合类指标

        Args:
            indicator_code: 指标代码
            data: 基础数据
            weight: 权重（绝对值，表示影响强度）
            direction: 方向（positive/negative/mixed）

        Returns:
            (最终得分, 归一化得分, 详细信息)
        """
        score = 0
        normalized = None
        detail = None

        # 连续跌停（分级）（负面指标）
        if indicator_code == 'continuous_limit_down':
            # 连续跌停分级扣分：
            # - 连跌2天：每只-4分
            # - 连跌3天：每只-6分
            # - 连跌4天：每只-8分
            # - 连跌5天及上：每只-10分

            # 计算原始负面得分
            raw_score = -(
                data.get('continuous_down_2day', 0) * 4 +
                data.get('continuous_down_3day', 0) * 6 +
                data.get('continuous_down_4day', 0) * 8 +
                data.get('continuous_down_5day_plus', 0) * 10
            )

            # 归一化到 [-1, 1] 范围
            # 假设最多20只连续跌停（5+天）作为上限：20 * 10 = 200
            max_possible = 200
            normalized = raw_score / max_possible if max_possible > 0 else 0
            normalized = max(-1, min(1, normalized))

            # 正权重 * 负归一化值 = 负分
            score = normalized * weight

            detail = {
                'count_2day': data.get('continuous_down_2day', 0),
                'count_3day': data.get('continuous_down_3day', 0),
                'count_4day': data.get('continuous_down_4day', 0),
                'count_5day_plus': data.get('continuous_down_5day_plus', 0)
            }

        # 指数配合度
        elif indicator_code == 'index_cooperation':
            # 指数配合度：
            # - 指数涨>1%：+5分
            # - 指数跌<-1%：-5分
            # - 其他情况：线性计算

            index_change = data.get('index_change', 0)

            if index_change > 1:
                raw_score = 5
            elif index_change < -1:
                raw_score = -5
            else:
                raw_score = index_change * 5  # -5到+5之间

            # 归一化到 [-1, 1]
            normalized = raw_score / 5  # 最大值为5
            normalized = max(-1, min(1, normalized))

            # 应用权重
            score = normalized * abs(weight)

            detail = {
                'change_percent': index_change,
                'threshold': 1
            }

        return score, normalized, detail
    
    def _get_mood_by_score(self, cursor: sqlite3.Cursor, score: float) -> Dict:
        """
        根据总分确定情绪等级
        
        Args:
            cursor: 数据库游标
            score: 总分
            
        Returns:
            情绪等级字典
        """
        try:
            cursor.execute('''
                SELECT mood_level, mood_name, score_min, score_max
                FROM market_mood_thresholds
                ORDER BY mood_level
            ''')
            
            thresholds = cursor.fetchall()
            
            for row in thresholds:
                mood_level = row[0]
                mood_name = row[1]
                score_min = row[2]
                score_max = row[3]
                
                if score_min <= score and (score_max == -999 or score <= score_max):
                    return {
                        'mood_level': mood_level,
                        'mood_name': mood_name,
                        'score_min': score_min,
                        'score_max': score_max
                    }
            
            # 如果没有找到匹配的阈值，返回默认值
            return {
                'mood_level': 3,
                'mood_name': '分化',
                'score_min': -7,
                'score_max': 8
            }
            
        except Exception as e:
            logger.error(f"获取情绪等级失败: {e}", exc_info=True)
            return {
                'mood_level': 3,
                'mood_name': '分化',
                'score_min': -7,
                'score_max': 8
            }
    
    def _save_calculation_result(
        self,
        cursor: sqlite3.Cursor,
        trade_date: str,
        total_score: float,
        mood: Dict,
        indicator_scores: Dict,
        normalized_scores: Dict,
        indicator_details: Dict
    ):
        """
        保存计算结果到数据库
        
        Args:
            cursor: 数据库游标
            trade_date: 交易日期
            total_score: 总分
            mood: 情绪等级
            indicator_scores: 各指标得分
            normalized_scores: 各指标归一化得分
            indicator_details: 各指标详细信息
        """
        cursor.execute('''
            INSERT OR REPLACE INTO market_mood_history (
                trade_date, total_score, normalized_scores, final_scores, 
                mood_level, mood_name, indicator_details
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade_date,
            total_score,
            json.dumps(normalized_scores, ensure_ascii=False),
            json.dumps(indicator_scores, ensure_ascii=False),
            mood['mood_level'],
            mood['mood_name'],
            json.dumps(indicator_details, ensure_ascii=False)
        ))
