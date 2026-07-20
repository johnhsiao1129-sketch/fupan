# 架构设计规则

## 核心原则

### ⚠️ 警告：避免过度耦合

**错误示例**：
```python
# ❌ 错误：通过参数判断不同功能
def fetch_and_save_limit_data(self, date: str, use_tmp_table: bool = False) -> Dict:
    """
    通用的统一数据获取函数
    - use_tmp_table=True: 今日首板盘中刷新
    - use_tmp_table=False: 连板梯队刷新
    """
    if use_tmp_table:
        # 今日首板逻辑
    else:
        # 连板梯队逻辑
```

**问题**：
- 修改今日首板功能可能影响连板梯队
- 需要检查所有调用方
- 逻辑分支复杂，难以维护
- **看似节约了代码，实际浪费了大量的时间和token！**

**正确示例**：
```python
# ✅ 正确：功能解耦，各自独立

# 文件：today_first_limits_intraday.py
def refresh_today_first_limits_intraday(date: str) -> Dict:
    """今日首板盘中刷新专用函数"""
    # 只获取今日首板数据
    # 只保存到临时表

# 文件：limit_stats_refresh.py  
def refresh_limit_stats(date: str) -> Dict:
    """连板梯队刷新专用函数"""
    # 获取首板+连板+炸板+跌停
    # 保存到正式表

# 文件：today_first_limits_postmarket.py
def refresh_today_first_limits_postmarket(date: str) -> Dict:
    """今日首板盘后刷新专用函数"""
    # 获取首板+连板+炸板+跌停
    # 保存到正式表
```

## 设计原则

### 1. 单一职责原则
- 每个函数只做一件事
- 每个模块负责一个独立的功能
- 一键刷新的按钮有对应的独立处理函数

### 2. 解耦原则
- 不同功能之间不共享代码
- 避免通过参数判断实现不同功能分支
- 功能A的修改不影响功能B

### 3. 可维护性优先
- **代码量宁可多一点，也要保证模块化**
- 逻辑清晰易于理解和调试
- 修改影响范围可控

### 4. 可读性优先
- 函数名称准确描述其功能
- 不需要阅读代码就知道函数在做什么
- 减少if-else分支，增加函数数量

### 5. 性能次之
- 只有在性能成为瓶颈时才考虑优化
- 不要为了"代码复用"而牺牲架构清晰度

## 检查清单

在编写新功能时，问自己：

- [ ] 这个函数是否只做一件事？
- [ ] 是否通过参数判断来实现不同功能？如果是，拆分成多个函数
- [ ] 如果要修改功能A，是否需要检查其他功能B、C、D？
- [ ] 如果答案都是"否"，说明代码已经充分解耦

## 实际案例

### Case 1: 今日首板盘中刷新

**需求**：盘中刷新时只获取今日首板，不获取炸板、跌停等数据

**错误设计**：
```python
def fetch_and_save_limit_data(use_tmp_table: bool):
    if use_tmp_table:
        # 跳过炸板、跌停
    else:
        # 获取所有
```

**正确设计**：
```python
def refresh_today_first_limits_intraday(date: str):
    """今日首板盘中刷新函数，只获取首板"""
    # 直接实现，不需要use_tmp_table判断
    pass
```

### Case 2: Mairui API接口调用

**错误设计**：
```python
def get_all_mairui_data(date: str, need_ztgc: bool, need_zbgc: bool, need_dtgc: bool):
    """通用获取函数，参数控制调用哪些接口"""
```

**正确设计**：
```python
def get_limit_down_data(date: str):
    """获取跌停数据"""

def get_exploded_data(date: str):
    """获取炸板数据"""

def get_strong_stock_data(date: str):
    """获取强势股数据"""
```

## 总结

> **代码复用是手段，不是目的**
> **真正的复用是针对相似的代码逻辑编写可复用的函数库**
> **而不是通过参数判断，把不同的功能揉在一起**

记住：**解耦 > 代码量**

---

## 最后更新

2026-03-12
