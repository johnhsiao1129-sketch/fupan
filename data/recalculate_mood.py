"""
清除市场情绪历史数据并重新计算
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from src.market_mood_calculator import MarketMoodCalculator

DB_PATH = "data/fupan.db"

def clear_mood_history():
    """清除market_mood_history表"""
    print("="*60)
    print("清除市场情绪历史数据...")
    print("="*60)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM market_mood_history')
        deleted = cursor.rowcount
        conn.commit()
        print(f"✓ 已清除 {deleted} 条历史记录")
        return True
    except Exception as e:
        conn.rollback()
        print(f"✗ 清除失败: {e}")
        return False
    finally:
        conn.close()

def recalculate_mood_data():
    """重新计算所有历史情绪数据"""
    print()
    print("="*60)
    print("重新计算市场情绪数据...")
    print("="*60)
    
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # 获取所有有涨跌停统计的交易日
        cursor = conn.cursor()
        cursor.execute('SELECT trade_date FROM limit_stats ORDER BY trade_date')
        dates = [row[0] for row in cursor.fetchall()]
        
        print(f"找到 {len(dates)} 个交易日")
        print()
        
        # 初始化计算器
        calculator = MarketMoodCalculator(DB_PATH)
        
        success_count = 0
        fail_count = 0
        
        for i, date in enumerate(dates, 1):
            try:
                result = calculator.calculate_market_mood(date)
                
                if result['success']:
                    success_count += 1
                    if i % 10 == 0 or i == len(dates):
                        print(f"  进度: {i}/{len(dates)} ({i*100//len(dates)}%) - {date}")
                else:
                    fail_count += 1
                    print(f"  ✗ {date}: {result.get('message', '失败')}")
                    
            except Exception as e:
                fail_count += 1
                print(f"  ✗ {date}: 计算失败 - {e}")
        
        calculator.close()
        
        print()
        print("="*60)
        print(f"✓ 计算完成: 成功 {success_count}, 失败 {fail_count}")
        print("="*60)
        
        return True
        
    except Exception as e:
        print(f"✗ 重新计算失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()

def verify_results():
    """验证计算结果"""
    print()
    print("="*60)
    print("验证计算结果...")
    print("="*60)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 统计各情绪等级数量
        cursor.execute('''
            SELECT mood_name, COUNT(*) as count
            FROM market_mood_history
            GROUP BY mood_name
            ORDER BY mood_name
        ''')
        rows = cursor.fetchall()
        
        print("\n情绪等级分布:")
        for row in rows:
            print(f"  {row[0]}: {row[1]} 天")
        
        # 显示最近10天的数据
        cursor.execute('''
            SELECT trade_date, total_score, mood_name
            FROM market_mood_history
            ORDER BY trade_date DESC
            LIMIT 10
        ''')
        rows = cursor.fetchall()
        
        print("\n最近10天数据:")
        for row in rows:
            print(f"  {row[0]}: {row[1]:.2f}分 - {row[2]}")
        
        return True
        
    except Exception as e:
        print(f"✗ 验证失败: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    print("\n" + "█" * 60)
    print("█" + " " * 58 + "█")
    print("█  清除并重新计算市场情绪数据  █")
    print("█" + " " * 58 + "█")
    print("█" * 60)
    print()
    
    # 1. 清除历史数据
    if clear_mood_history():
        # 2. 重新计算
        if recalculate_mood_data():
            # 3. 验证结果
            verify_results()
            print("\n✅ 全部完成!")
            sys.exit(0)
        else:
            print("\n✗ 重新计算失败")
            sys.exit(1)
    else:
        print("\n✗ 清除历史数据失败")
        sys.exit(1)
