-- ========================================
-- 市场情绪计算系统 - 配置数据初始化脚本
-- ========================================
-- 说明：本脚本用于初始化市场情绪计算的配置数据
-- 执行方法：sqlite3 data/fupan.db < data/init_market_mood_config.sql
-- ========================================


-- ========================================
-- 1. 情绪等级划分（状态定义）
-- 说明：
-- - 冰点: 市场最冷，首板<中位数50%，跌停>10，题材稀少
-- - 回升: 止跌企稳，首板接近中位数，开始有接力
-- - 分化: 题材轮动快，高低切换，情绪分化
-- - 爆发: 首板>中位数20%，梯队完整，题材共振
-- - 高潮: 首板>中位数50%，5板+打开，炸板率>20%
-- ========================================

DELETE FROM market_mood_thresholds;

INSERT INTO market_mood_thresholds 
(mood_level, mood_name, score_min, score_max, description, color_code) 
VALUES
(1, '冰点', -999, -20, 
 '市场情绪冰点：首板严重不足（低于中位数30%+），跌停>10，题材稀少，无接力', 
 '#3498db'),

(2, '回升', -19, -8, 
 '市场情绪回升：首板接近中位数，开始有接力，跌停减少，题材逐步活跃', 
 '#1abc9c'),

(3, '分化', -7, 8, 
 '市场情绪分化：首板在中位数附近，炸板率10-15%，题材轮动快，高低切频繁', 
 '#9b59b6'),

(4, '爆发', 9, 25, 
 '市场情绪爆发：首板>中位数20%，连板活跃，梯队完整，题材共振，跌停<5', 
 '#e74c3c'),

(5, '高潮', 26, -999, 
 '市场情绪高潮：首板>中位数50%，连板高度打开（5板+），炸板率>20%，题材疯涨', 
 '#e67e22');


-- ========================================
-- 2. 基础指标配置
-- 说明：
-- - weight: 权重，影响最终得分的贡献度
-- - baseline: 归一化基准（用于计算归一化得分）
-- - direction: 方向（positive=正向加分, negative=负向扣分, mixed=双向）
-- - is_enabled: 是否启用（0=禁用，1=启用）
-- ========================================

DELETE FROM market_mood_config;

-- ===== A类：基础数据指标 (8个) =====

-- A1: 首板 vs 中位数
-- 归一化公式：(首板数 - 中位数) / (中位数 * 0.5)
-- 范围：[-2, +2]，实际限制在[-1, 1]
-- 说明：首板是市场最直接的激活度指标
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('basic', '首板vs中位数', 'first_limit_vs_median', 'mixed', 8.0, NULL, 
 '{"formula": "(首板数 - 中位数) / (中位数 * 0.5) × 8", "range": "[-1, 1]", "source": "limit_stats.first_limit"}', 
 1);

-- A2: 连板 vs 中位数 (暂不启用)
-- 等数据稳定后再启用
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('basic', '连板vs中位数', 'continuous_vs_median', 'mixed', 6.0, NULL, 
 '{"formula": "(连板数 - 中位数) / (中位数 * 0.5) × 6", "range": "[-1, 1]", "source": "limit_stats.continuous_limit"}', 
 0);

-- A3: 历史新高 (最强信号)
-- 权重最高，反映超级周期的启动
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('basic', '历史新高', 'all_time_high', 'positive', 10.0, NULL, 
 '{"formula": "(历史新高数 - 中位数) / (中位数 * 0.5) × 10", "range": "[-1, 1]", "source": "daily_highs.all_time_high_count"}', 
 1);

-- A4: 一年新高 (强趋势)
-- 权重次之，反映当前热点和强势股
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('basic', '一年新高', 'one_year_high', 'positive', 7.0, NULL, 
 '{"formula": "(一年新高数 - 中位数) / (中位数 * 0.5) × 7", "range": "[-1, 1]", "source": "daily_highs.one_year_high_count"}', 
 1);

-- A5: 半年新高 (中等强度)
-- 补充说明市场热度
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('basic', '半年新高', 'six_month_high', 'positive', 4.0, NULL, 
 '{"formula": "(半年新高数 - 中位数) / (中位数 * 0.5) × 4", "range": "[-1, 1]", "source": "daily_highs.six_month_high_count"}', 
 1);

-- A6: 炸板率 (负向)
-- 炸板率=炸板/(首板+连板+炸板)
-- 基准15%算正常
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('basic', '炸板率', 'explode_rate', 'negative', -7.0, 15, 
 '{"formula": "(炸板率 - 15) / (15 * 0.5) × 7", "range": "[-1, 1]", "source": "limit_stats.explode_rate"}', 
 1);

-- A7: 跌停 vs 中位数 (暂不启用)
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('basic', '跌停vs中位数', 'limit_down_vs_median', 'mixed', -10.0, NULL, 
 '{"formula": "(跌停数 - 中位数) / (中位数 * 0.5) × 10", "range": "[-1, 1]", "source": "limit_stats.limit_down"}', 
 0);

-- A8: 跌停基础数量
-- 基准5个，权重最重
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('basic', '跌停数量', 'limit_down_base', 'negative', -5.0, 5, 
 '{"formula": "(跌停数 - 5) / (5 * 0.5) × 5", "range": "[-1, 1]", "source": "limit_stats.limit_down"}', 
 1);


-- ===== B类：连板梯队指标 (6个) =====

-- B1: 梯队完整性
-- 核心：从2板开始，向上逐层检查是否连续存在
-- 计算逻辑：连续完整层数 × 2 - 10（归一化处理）
-- 9板及以上妖股不参与完整性计算
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('continuity', '梯队完整性', 'ladder_completeness', 'positive', 8.0, NULL, 
 '{"formula": "连续完整层数 × 2 - 10", "criterion": "从2板开始向上计数，断层即停止", "exclude_tier": "9+"}', 
 1);

-- B2: 5板及以上股票
-- 每只5板积2分
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('continuity', '5板及以上', 'height_5plus', 'positive', 6.0, NULL, 
 '{"formula": "数量 × 2", "tier_high": 5, "score_per_stock": 2}', 
 1);

-- B3: 6板及以上股票
-- 每只6板积2.5分
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('continuity', '6板及以上', 'height_6plus', 'positive', 10.0, NULL, 
 '{"formula": "数量 × 2.5", "tier_high": 6, "score_per_stock": 2.5}', 
 1);

-- B4: 7板及以上股票
-- 每只7板积3分（妖股级高度）
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('continuity', '7板及以上', 'height_7plus', 'positive', 12.0, NULL, 
 '{"formula": "数量 × 3", "tier_high": 7, "score_per_stock": 3, "note": "妖股级高度"}', 
 1);

-- B5: 梯队饱满度 (分层次)
-- 饱满度阈值分层：
--   - 2板: ≥7个+2分，<3个-2分
--   - 3板: ≥5个+2分，<2个-2分
--   - 4板: ≥3个+2分，<1个-2分
--   - 5板: ≥2个+2分，<1个-2分
--   - 6层及以上: 不计算（太稀有）
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('continuity', '梯队饱满度', 'ladder_fullness', 'mixed', 4.0, NULL, 
 '{"formula": "分层评分累计", "thresholds": {"2": {"high": 7, "low": 3}, "3": {"high": 5, "low": 2}, "4": {"high": 3, "low": 1}, "5": {"high": 2, "low": 1}}, "exclude_tier": "6+"}', 
 1);

-- B6: 最高层突破
-- 今日最高连板 > 近7日纪录，积6分
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('continuity', '最高层突破', 'top_tier_breakthrough', 'positive', 6.0, NULL, 
 '{"formula": "是否突破近7日纪录? 6:0", "source": "近7日最高连板比较"}', 
 1);


-- ===== C类：题材延续性指标 (3个) =====

-- C1: 题材活跃度 (状态化)
-- 状态分类：
--   - 正向（启动/爆发）: +0.5分/天
--   - 中性（维持）: 0分/天
--   - 负向（分歧/退潮）: -0.3分/天
--   - 特殊（回流）: +0.3分/天（从负转正）
-- 连续性：断档≤5天算连续
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('topic', '题材活跃度', 'topic_activity', 'mixed', 8.0, NULL, 
 '{"formula": "各题材活跃度积分之和", "state_weights": {"startup": 0.5, "explosion": 0.5, "maintain": 0, "divergence": -0.3, "recede": -0.3, "backflow": 0.3}, "continuity": "断档≤5天算连续"}', 
 1);

-- C2: 热点题材数量
-- 热点题材定义：首板≥5且接力良好
-- 每个热点题材积1.5分
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('topic', '热点题材数量', 'hot_topics_count', 'positive', 5.0, NULL, 
 '{"formula": "热点题材数量 × 1.5", "definition": "首板≥5且接力良好"}', 
 1);

-- C3: 单题材首板强度
-- 归一化：最高强度题材的天数 / 10（10天算满分）
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('topic', '单题材首板强度', 'topic_strength', 'positive', 3.0, NULL, 
 '{"formula": "max(强度天数 / 10, 1) × 3", "source": "topic_continuity.continuity_days"}', 
 1);


-- ===== D类：综合指标 (2个) =====

-- D1: 连续跌停（分级）
-- 分级扣分：
--   - 连跌2天：每只-4分
--   - 连跌3天：每只-6分
--   - 连跌4天：每只-8分
--   - 连跌5天+: 每只-10分
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('integration', '连续跌停分级', 'continuous_limit_down', 'negative', -12.0, NULL, 
 '{"formula": "分层数量 × 对应权重", "weights": {"2day": 4, "3day": 6, "4day": 8, "5day_plus": 10}}', 
 1);

-- D2: 指数配合度
-- 指数涨跌幅配合：
--   - 涨>1%: +5分
--   - 跌<-1%: -5分
--   - 其他: 线性计算
INSERT INTO market_mood_config 
(category, indicator_name, indicator_code, direction, weight, baseline, 
 calculation_rule, is_enabled) 
VALUES
('integration', '指数配合度', 'index_cooperation', 'mixed', 5.0, NULL, 
 '{"formula": "指数涨跌幅 × 5, 范围[-5, +5]", "thresholds": {"up": 1, "down": -1}}', 
 1);


-- ========================================
-- 初始化完成提示
-- ========================================

.print ''
.print '========================================'
.print '  市场情绪计算系统配置数据初始化完成！'
.print '========================================'
.print ''
.print '已初始化数据：'
.print '  - 情绪等级：5个（冰点/回升/分化/爆发/高潮）'
.print '  - 基础指标：8个'
.print '  - 连板梯队指标：6个'
.print '  - 题材延续性指标：3个'
.print '  - 综合指标：2个'
.print '  合计：19个指标'
.print ''
.print '使用说明：'
.print '  1. 调整权重：UPDATE market_mood_config SET weight=新值 WHERE indicator_code="...";'
.print '  2. 禁用指标：UPDATE market_mood_config SET is_enabled=0 WHERE indicator_code="...";'
.print '  3. 调整状态阈值：UPDATE market_mood_thresholds SET score_min=新值 WHERE mood_level=...;'
.print ''
