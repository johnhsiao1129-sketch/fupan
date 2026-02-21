"""
修复 first_limit_topics 表中失效的 first_limit_id

问题：
- 首板数据刷新后，旧的 first_limit_id 被删除
- first_limit_topics 表中的 first_limit_id 指向已删除的记录
- 导致题材轮动分析和首板板块无法显示股票

修复方案：
1. 查找出所有失效的 first_limit_id（不在 first_limits 表中）
2. 对于每个失效记录，根据 topic_id 和 association_date：
   a. 在 topic_stock_relations 中查找关联的所有 stock_id（date=association_date, relation_type='first_limit'）
   b. 在 first_limits 中查找每个 stock_id 对应的 first_limit_id（stock_id + limit_date=association_date）
3. 更新 first_limit_topics 表中的 first_limit_id（所有找到的都更新）
"""

import sqlite3
import sys
import os
import traceback

# 添加项目路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

DB_PATH = 'data/fupan.db'

def fix_first_limit_topics():
    """修复失效的 first_limit_id"""
    print("=" * 60)
    print("开始修复 first_limit_topics 表中的失效 first_limit_id")
    print("=" * 60)
    print()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = OFF")  # 暂时关闭外键约束
    
    try:
        # 步骤1：找出所有失效的 first_limit_id（不在 first_limits 表中）
        print("步骤1：查找所有失效的 first_limit_id...")
        cursor.execute('''
            SELECT flt.id, flt.first_limit_id, flt.topic_id, flt.association_date, t.topic_name
            FROM first_limit_topics flt
            LEFT JOIN first_limits fl ON flt.first_limit_id = fl.id
            LEFT JOIN topics t ON flt.topic_id = t.topic_id
            WHERE fl.id IS NULL
            ORDER BY flt.association_date DESC, flt.topic_id
        ''')
        
        orphaned_records = cursor.fetchall()
        print(f"找到 {len(orphaned_records)} 条失效的 first_limit_id 记录\n")
        
        if len(orphaned_records) == 0:
            print("✓ 没有失效记录，无需修复")

            return {
                "total": 0,
                "already_correct": 0,
                "fixed": 0,
                "not_found": 0
            }
        
        # 步骤2：为每条失效记录查找正确的新 first_limit_id
        print("步骤2：查找正确的 first_limit_id...")
        update_map = []
        not_found_count = 0
        total_processed = 0
        
        for record in orphaned_records:
            ft_id = record[0]
            old_first_limit_id = record[1]
            topic_id = record[2]
            association_date = record[3]
            topic_name = record[4]
            
            total_processed += 1
            
            print(f"[{total_processed}/{len(orphaned_records)}] 处理: {topic_name} ({association_date})")
            print(f"  旧ID: {old_first_limit_id}")
            
            # 在 topic_stock_relations 中查找关联的所有 stock_id
            cursor.execute('''
                SELECT stock_id
                FROM topic_stock_relations
                WHERE topic_id = ? AND date = ? AND relation_type = 'first_limit'
            ''', (topic_id, association_date))
            
            relations = cursor.fetchall()
            print(f"  找到 {len(relations)} 个关联股票")
            
            # 为找到的所有 stock_id 查找对应的 first_limit_id
            found_count = 0
            for relation in relations:
                stock_id = relation[0]
                
                cursor.execute('''
                    SELECT id
                    FROM first_limits
                    WHERE stock_id = ? AND limit_date = ?
                ''', (stock_id, association_date))
                
                first_limit = cursor.fetchone()
                if first_limit:
                    new_first_limit_id = first_limit[0]
                    update_map.append({
                        'id': ft_id,
                        'old_id': old_first_limit_id,
                        'new_id': new_first_limit_id,
                        'topic_id': topic_id,
                        'topic_name': topic_name,
                        'association_date': association_date
                    })
                    print(f"  ✓ 修复: {old_first_limit_id} → {new_first_limit_id}")
                    found_count += 1
            
            if found_count == 0:
                print(f"  ✗ 未找到匹配的 first_limit_id")
                not_found_count += 1
            
            print()
        
        print(f"步骤2完成：共找到修复方案 {len(update_map)} 个（{not_found_count} 个未找到）\n")
        
        # 步骤3：批量更新修复
        print("步骤3：批量更新 first_limit_id...")
        updated_count = 0
        
        for update in update_map:
            try:
                # 先检查新组合是否违反 UNIQUE 约束
                cursor.execute('''
                    SELECT id FROM first_limit_topics
                    WHERE first_limit_id = ? AND topic_id = ? AND association_date = ?
                    AND id != ?
                ''', (update['new_id'], update['topic_id'], update['association_date'], update['id']))
                
                conflict = cursor.fetchone()
                if conflict:
                    # 新组合已在其他记录中，删除当前记录
                    print(f"  ⚠️  组合冲突，删除记录 id={update['id']}")
                    cursor.execute('DELETE FROM first_limit_topics WHERE id = ?', (update['id'],))
                else:
                    # 更新记录
                    cursor.execute('''
                        UPDATE first_limit_topics
                        SET first_limit_id = ?
                        WHERE id = ?
                    ''', (update['new_id'], update['id']))
                    updated_count += 1
                    print(f"  ✓ 更新记录 id={update['id']}")
            except Exception as e:
                print(f"  ✗ 更新失败 id={update['id']}: {e}")
        
        conn.commit()
        print(f"\n步骤3完成：共处理 {updated_count} 条修复\n")
        
        # 输出结果
        print("=" * 60)
        print("修复完成")
        print("=" * 60)
        print(f"总失效记录: {len(orphaned_records)} 条")
        print(f"已修复: {updated_count} 条")
        print(f"未找到: {not_found_count} 条")
        print()
        
        return {
            "total": len(orphaned_records),
            "already_correct": 0,
            "fixed": updated_count,
            "not_found": not_found_count
        }
        
    except Exception as e:
        print(f"❌ 修复失败: {e}")
        traceback.print_exc()
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()

def print_topic_stats(after_fix=False):
    """打印题材统计数据"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                t.topic_id,
                t.topic_name,
                ft.association_date,
                COUNT(*) as count
            FROM first_limit_topics ft
            JOIN topics t ON ft.topic_id = t.topic_id
            GROUP BY t.topic_id, ft.association_date
            ORDER BY ft.association_date DESC, t.topic_name
        ''')
        
        stats = cursor.fetchall()
        
        if after_fix:
            print("\n" + "=" * 60)
            print("题材统计数据（修复后）")
            print("=" * 60)
        else:
            print("=" * 60)
            print("题材统计数据（修复前）")
            print("=" * 60)
        
        for row in stats:
            topic_name = row[1]
            date = row[2]
            count = row[3]
            print(f"{topic_name:20s} {date}: {count}只")
        
        print()
        conn.close()
        
    except Exception as e:
        print(f"打印统计数据失败: {e}")

if __name__ == '__main__':
    result = fix_first_limit_topics()
    
    if result and result.get('fixed', 0) > 0:
        print_topic_stats(after_fix=True)
