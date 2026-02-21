"""
使用Python执行SQL初始化配置数据
"""
import sqlite3
import sys

db_path = "data/fupan.db"

print("开始初始化配置数据...")

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 读取SQL文件（清理版）
    with open('data/init_clean.sql', 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    # 分割SQL语句
    sql_statements = [stmt.strip() for stmt in sql_content.split(';') if stmt.strip()]
    
    # 执行每个SQL语句
    for i, stmt in enumerate(sql_statements, 1):
        try:
            # 跳过注释和打印语句
            if stmt.startswith('--') or stmt.startswith('.'):
                continue
            
            cursor.execute(stmt)
            print(f"✓ 执行第{i}条SQL成功")
        except Exception as e:
            print(f"✗ 第{i}条SQL执行失败: {e}")
    
    # 提交事务
    conn.commit()
    
    # 验证配置数据
    cursor.execute('SELECT COUNT(*) FROM market_mood_config')
    config_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM market_mood_thresholds')
    threshold_count = cursor.fetchone()[0]
    
    print(f"\n✅ 配置数据初始化完成！")
    print(f"  - 指标配置：{config_count}条")
    print(f"  - 状态阈值：{threshold_count}条")
    
    conn.close()
    
except Exception as e:
    print(f"❌ 初始化失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
