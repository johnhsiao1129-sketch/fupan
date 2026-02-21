# 市场情绪计算系统 - 使用文档

## 📌 概述

市场情绪计算系统是一个独立的模块，用于根据多维度指标计算市场情绪积分，判断市场处于哪个阶段（冰点/回升/分化/爆发/高潮）。

## 📁 文件结构

```
/
├── src/
│   └── market_mood_calculator.py    # 核心计算器（算法实现）
├── data/
│   ├── migration_market_mood.py     # 数据库迁移脚本（创建表）
│   ├── init_market_mood_config.sql  # 配置数据初始化脚本
│   └── test_market_mood.py          # 测试脚本（待创建）
└── README_MARKET_MOOD.md            # 本文档
```

## 🚀 快速开始

### 1. 初始化数据库

#### 1.1 创建表结构

```bash
python data/migration_market_mood.py
```

脚本会询问是否创建表，输入 `y` 确认。

#### 1.2 初始化配置数据

```bash
sqlite3 data/fupan.db < data/init_market_mood_config.sql
```

### 2. 使用计算器

#### 2.1 Python代码使用

```python
from src.market_mood_calculator import MarketMoodCalculator

# 创建计算器实例
calculator = MarketMoodCalculator('data/fupan.db')

# 计算指定日期的市场情绪
result = calculator.calculate_market_mood('2026-02-14')

# 查看结果
print(f"总分: {result['total_score']}")
print(f"情绪: {result['mood_name']} ({result['mood_level']})")
print(f"各指标得分: {result['indicator_scores']}")
print(f"详细信息: {result['indicator_details']}")

# 关闭连接
calculator.close()
```

#### 2.2 输出格式

```python
{
    'success': True,
    'trade_date': '2026-02-14',
    'total_score': 12.5,
    'mood_level': 4,
    'mood_name': '爆发',
    'indicator_scores': {
        'first_limit_vs_median': 4.5,
        'all_time_high': 3.2,
        'ladder_completeness': 2.0,
        ...
    },
    'normalized_scores': {
        'first_limit_vs_median': 0.5625,
        'all_time_high': 0.32,
        ...
    },
    'indicator_details': {
        'first_limit_vs_median': {
            'value': 45,
            'median': 30,
            'diff': 15,
            'ratio': 1.5
        },
        ...
    }
}
```

## 📊 指标体系

### A类：基础数据指标 (8个)

| 指标代码 | 指标名称 | 权重 | 状态 | 说明 |
|----------|----------|------|------|------|
| `first_limit_vs_median` | 首板vs中位数 | +8.0 | ✅启用 | 首板超出/不足 |
| `continuous_vs_median` | 连板vs中位数 | +6.0 | ⏸️待启用 | 连板超出/不足 |
| `all_time_high` | 历史新高 | +10.0 | ✅启用 | 最强信号 |
| `one_year_high` | 一年新高 | +7.0 | ✅启用 | 强趋势信号 |
| `six_month_high` | 半年新高 | +4.0 | ✅启用 | 中等强度 |
| `explode_rate` | 炸板率 | -7.0 | ✅启用 | 负向指标 |
| `limit_down_vs_median` | 跌停vs中位数 | -10.0 | ⏸️待启用 | 跌停超出/不足 |
| `limit_down_base` | 跌停数量 | -5.0 | ✅启用 | 基础跌停 |

### B类：连板梯队指标 (6个)

| 指标代码 | 指标名称 | 权重 | 说明 |
|----------|----------|------|------|
| `ladder_completeness` | 梯队完整性 | +8.0 | 从2板开始向上检查连续性 |
| `height_5plus` | 5板及以上 | +6.0 | 每只+2分 |
| `height_6plus` | 6板及以上 | +10.0 | 每只+2.5分 |
| `height_7plus` | 7板及以上 | +12.0 | 每只+3分（妖股级）|
| `ladder_fullness` | 梯队饱满度 | +4.0 | 分层评分累计 |
| `top_tier_breakthrough` | 最高层突破 | +6.0 | 突破近7日纪录 |

### C类：题材延续性指标 (3个)

| 指标代码 | 指标名称 | 权重 | 说明 |
|----------|----------|------|------|
| `topic_activity` | 题材活跃度 | +8.0 | 状态化计算 |
| `hot_topics_count` | 热点题材数量 | +5.0 | 每个热点+1.5分 |
| `topic_strength` | 单题材首板强度 | +3.0 | 归一化计算 |

### D类：综合指标 (2个)

| 指标代码 | 指标名称 | 权重 | 说明 |
|----------|----------|------|------|
| `continuous_limit_down` | 连续跌停分级 | -12.0 | 分层扣分 |
| `index_cooperation` | 指数配合度 | +/-5.0 | 指数配合 |

## ⚙️ 配置调整

### 调整权重

```sql
-- 调整首板权重从8改为10
UPDATE market_mood_config 
SET weight = 10.0 
WHERE indicator_code = 'first_limit_vs_median';
```

### 禁用/启用指标

```sql
-- 禁用某个指标
UPDATE market_mood_config 
SET is_enabled = 0 
WHERE indicator_code = 'continuous_vs_median';

-- 启用某个指标
UPDATE market_mood_config 
SET is_enabled = 1 
WHERE indicator_code = 'continuous_vs_median';
```

### 调整状态阈值

```sql
-- 调整"爆发"状态的下界从9改为12
UPDATE market_mood_thresholds 
SET score_min = 12 
WHERE mood_name = '爆发';
```

## 📝 数据归一化说明

### 归一化公式

对于相对中位数的指标：
```
归一化得分 = (当前值 - 中位数) / (中位数 × 0.5)
```

**结果范围**：[-2, +2]，实际限制在[-1, 1]

**示例**：
- 首板中位数30，今日首板45：
  - 差值：45 - 30 = 15
  - 归一化：15 / (30 × 0.5) = 1.0
  - 限制在[-1, 1]：1.0
  - 最终得分：1.0 × 8 = 8分

### 归一化的好处

1. **量级统一**：不同指标的量级统一到[-1, 1]
2. **权重合理**：权重真正反映指标重要性
3. **便于调整**：调整权重即可影响最终得分

## 🔧 数据准备

### 必需的基础表

系统依赖以下表的数据：

1. **limit_stats** - 涨跌停统计（已有）
   - first_limit - 首板数量
   - continuous_limit - 连板数量
   - exploded - 炸板数量
   - explode_rate - 炸板率
   - limit_down - 跌停数量

2. **daily_highs** - 每日新高数据（新增）
   - all_time_high_count
   - one_year_high_count
   - six_month_high_count

3. **continuous_limits_detail** - 连板梯队详情（新增）
   - tier_height - 连板高度（2,3,4,5,6,7...）
   - stock_count - 该高度股票数量

4. **topic_continuity** - 题材延续性（新增）
   - topic_name - 题材名称
   - topic_stage - 题材状态
   - continuity_days - 连续活跃天数

5. **daily_lows** - 每日低点数据（新增）
   - count_2day - 连跌2天数量
   - count_3day - 连跌3天数量
   - count_4day - 连跌4天数量
   - count_5day_plus - 连跌5天及以上数量

6. **market_index** - 大盘指数（可选）
   - change_percent - 涨跌幅

## ⚠️ 注意事项

1. **数据完整性**：所有指标需要对应的数据表有数据
2. **中位数计算**：系统自动计算最近30天的中位数
3. **配置优先**：调整配置时优先更新 `market_mood_config` 表
4. **历史回溯**：可以计算历史任何一天的情绪
5. **结果保存**：计算结果自动保存到 `market_mood_history` 表

## 🧪 测试

### 测试脚本

```python
from src.market_mood_calculator import MarketMoodCalculator

calculator = MarketMoodCalculator('data/fupan.db')

# 测试最近的数据
result = calculator.calculate_market_mood('2026-02-14')

if result['success']:
    print(f"✅ 计算成功")
    print(f"日期: {result['trade_date']}")
    print(f"总分: {result['total_score']}")
    print(f"情绪: {result['mood_name']} (等级{result['mood_level']})")
    print(f"\n各指标得分:")
    for code, score in result['indicator_scores'].items():
        print(f"  {code}: {score:.2f}")
else:
    print(f"❌ 计算失败: {result['message']}")

calculator.close()
```

## 📞 问题反馈

如果遇到问题，请检查：

1. 数据库表是否正确创建
2. 配置数据是否正确初始化
3. 基础数据表是否有对应日期的数据
4. 数据库连接是否正常

---

**最后更新**：2026-02-14
**版本**：v1.0
