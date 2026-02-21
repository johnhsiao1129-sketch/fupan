"""
将历史跌停数据导入到 limit_down_history 表
说明：
- 从 limit_down 表读取历史跌停数据
- 计算每只股票的连续跌停天数
- 保存到 limit_down_history 表
"""

import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = "../data/fupan.db"


def import_limit_down_history():
    """导入跌停历史数据"""
    print("=" * 60)
    print("开始导入跌停历史数据到 limit_down_history...")
    print("=" * 60)

    if not os.path.exists(DB_PATH):
        print(f"❌ 数据库文件不存在：{DB_PATH}")
        return False

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 获取所有跌停记录
        print("\n[1/3] 获取跌停数据...")
        cursor.execute('''
            SELECT ld.stock_id, s.stock_code, s.stock_name, s.industry, 
                   ld.trade_date, ld.price, ld.change_percent, ld.amount, ld.reason
            FROM limit_down ld
            JOIN stocks s ON ld.stock_id = s.stock_id
            ORDER BY ld.trade_date DESC
        ''')

        limit_down_records = cursor.fetchall()
        print(f"✓ 获取到 {len(limit_down_records)} 条跌停记录")

        # 按股票分组
        print("\n[2/3] 计算连续跌停天数...")
        stock_downs = {}
        for record in limit_down_records:
            stock_code = record[1]
            if stock_code not in stock_downs:
                stock_downs[stock_code] = []
            stock_downs[stock_code].append({
                'stock_id': record[0],
                'code': stock_code,
                'name': record[2],
                'sector': record[3],
                'trade_date': record[4],
                'price': record[5],
                'change_percent': record[6],
                'amount': record[7],
                'reason': record[8]
            })

        # 按日期排序
        for stock_code in stock_downs:
            stock_downs[stock_code].sort(key=lambda x: datetime.strptime(x['trade_date'], '%Y-%m-%d'))

        # 计算连续跌停天数并保存
        print("\n[3/3] 保存到 limit_down_history 表...")
        saved_count = 0

        for stock_code, downs in stock_downs.items():
            # 找出连续跌停的段落
            i = 0
            while i < len(downs):
                current_down = downs[i]
                continuous_days = 1

                # 向前查找连续的日期
                j = i + 1
                while j < len(downs):
                    current_date = datetime.strptime(downs[j]['trade_date'], '%Y-%m-%d')
                    prev_date = datetime.strptime(downs[j-1]['trade_date'], '%Y-%m-%d')
                    days_diff = (current_date - prev_date).days

                    if days_diff == 1:
                        continuous_days += 1
                        j += 1
                    else:
                        break

                # 如果连续跌停天数>=2，保存每条记录
                if continuous_days >= 2:
                    for k in range(i, j):
                        record = downs[k]
                        create_time = f"{record['trade_date']}T16:00:00"

                        cursor.execute('''
                            INSERT OR REPLACE INTO limit_down_history
                            (trade_date, code, name, price, change_percent, continuous_days, sector, reason, amount, create_time)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            record['trade_date'],
                            record['code'],
                            record['name'],
                            record['price'],
                            record['change_percent'],
                            continuous_days,
                            record['sector'] or '',
                            record['reason'] or '',
                            record['amount'],
                            create_time
                        ))
                        saved_count += 1

                i = j

        conn.commit()

        print(f"✓ 保存完成: {saved_count} 条记录")

        # 统计结果
        print("\n统计结果:")
        cursor.execute('''
            SELECT continuous_days, COUNT(*) as count
            FROM limit_down_history
            GROUP BY continuous_days
            ORDER BY continuous_days
        ''')

        for row in cursor.fetchall():
            print(f"  连续{row[0]}天跌停: {row[1]} 只股票")

        # 列出连续2天及以上跌停的股票
        cursor.execute('''
            SELECT code, name, continuous_days, trade_date, price, change_percent
            FROM limit_down_history
            ORDER BY continuous_days DESC, trade_date DESC
            LIMIT 20
        ''')

        print("\n连续跌停股票详情（前20条）:")
        for row in cursor.fetchall():
            print(f"  {row[0]} {row[1]}: {row[2]}天, {row[3]} ({row[4]:.2f}, {row[5]:.2f}%)")

        print("\n" + "=" * 60)
        print("✅ 跌停历史数据导入完成！")
        print("=" * 60)

        return True

    except Exception as e:
        conn.rollback()
        print(f"\n❌ 导入失败：{e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        conn.close()


if __name__ == '__main__':
    print("\n跌停历史数据导入脚本")
    print("是否要执行导入？(y/n)")
    answer = input("> ").strip().lower()

    if answer in ['y', 'yes']:
        if import_limit_down_history():
            print("\n✅ 导入成功！")
        else:
            print("\n❌ 导入失败！")
    else:
        print("\n已取消导入")
