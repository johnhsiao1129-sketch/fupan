"""
盘中记录模块 - 独立模块

提供盘中手动记录盘面信息的功能，支持：
- 系统时间自动（仅交易时段）
- 90s 内合并
- 同一时间合并
- 事后编辑时间（受前后条目约束）
"""
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.database import DB_PATH

logger = logging.getLogger(__name__)


def _is_in_trading_hours() -> bool:
    """懒加载调用 main.is_in_trading_hours（避免循环导入）"""
    from main import is_in_trading_hours
    return is_in_trading_hours()


def _get_query_trading_date() -> str:
    """懒加载调用 main.get_query_trading_date（避免循环导入）"""
    from main import get_query_trading_date
    return get_query_trading_date()

TRADING_START = "09:25"
TRADING_END = "15:00"
MERGE_THRESHOLD_SEC = 90


def _get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _now_time() -> str:
    return datetime.now().strftime("%H:%M")


def _now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def can_use_system_time() -> bool:
    """是否可以使用系统时间作为新条目的时间（仅交易时段内）"""
    return _is_in_trading_hours()


def get_time_picker_range(prev_time: Optional[str] = None,
                          next_time: Optional[str] = None) -> Dict[str, str]:
    """返回手动选时间的可选范围

    Args:
        prev_time: 前一条目的时间（HH:MM），用于约束下界
        next_time: 后一条目的时间（HH:MM），用于约束上界

    Returns:
        {"min": "HH:MM", "max": "HH:MM"}
    """
    start = TRADING_START
    end = TRADING_END
    if prev_time:
        start = max(start, prev_time)
    if next_time:
        end = min(end, next_time)
    return {"min": start, "max": end}


def get_time_rules(target_date: str, prev_time: Optional[str] = None,
                   next_time: Optional[str] = None) -> Dict[str, Any]:
    """获取时间规则（供前端初始化使用）

    Returns:
        {
            "can_use_system": bool,
            "current_time": "HH:MM" or None,
            "range": {"min": "HH:MM", "max": "HH:MM"},
            "is_trading_date": bool,
        }
    """
    today = _get_query_trading_date()
    is_trading_date = target_date == today
    in_trading_hours = _is_in_trading_hours()
    can_system = is_trading_date and in_trading_hours

    return {
        "can_use_system": can_system,
        "current_time": _now_time() if can_system else None,
        "range": get_time_picker_range(prev_time, next_time),
        "is_trading_date": is_trading_date,
        "in_trading_hours": in_trading_hours,
    }


def resolve_merge_action(target_date: str,
                         candidate_time: str,
                         prev_entry: Optional[Dict] = None,
                         next_entry: Optional[Dict] = None,
                         is_system_time: bool = False,
                         last_input_created_at: Optional[str] = None) -> Dict[str, Any]:
    """判断候选时间的操作动作（合并 vs 新建）

    Returns:
        {"action": "merge"|"new"|"update"|"error", "target_id"?: int, "note_time"?: str, "msg"?: str}
    """
    if prev_entry and candidate_time == prev_entry["note_time"]:
        return {"action": "merge", "target_id": prev_entry["id"]}

    if next_entry and candidate_time == next_entry["note_time"]:
        return {"action": "merge", "target_id": next_entry["id"]}

    if is_system_time and prev_entry and last_input_created_at:
        try:
            last_dt = datetime.fromisoformat(last_input_created_at)
            elapsed = (datetime.now() - last_dt).total_seconds()
            if elapsed < MERGE_THRESHOLD_SEC:
                return {"action": "merge", "target_id": prev_entry["id"]}
        except Exception:
            pass

    if prev_entry and candidate_time <= prev_entry["note_time"]:
        return {"action": "error", "msg": f"时间必须晚于上一条 {prev_entry['note_time']}"}
    if next_entry and candidate_time >= next_entry["note_time"]:
        return {"action": "error", "msg": f"时间必须早于下一条 {next_entry['note_time']}"}

    return {"action": "new_or_update", "note_time": candidate_time}


def list_notes(trade_date: str) -> List[Dict[str, Any]]:
    """获取指定日期的所有盘中记录（按 note_time 排序）"""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, trade_date, note_time, content, is_manual_time, created_at, updated_at
        FROM intraday_notes
        WHERE trade_date = ?
        ORDER BY note_time ASC
    ''', (trade_date,))
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": r[0],
            "trade_date": r[1],
            "note_time": r[2],
            "content": r[3],
            "is_manual_time": bool(r[4]),
            "created_at": r[5],
            "updated_at": r[6],
        }
        for r in rows
    ]


def _get_entry_for_merge(trade_date: str, candidate_time: str) -> Optional[Dict[str, Any]]:
    """根据时间查找现有条目（用于合并）"""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, trade_date, note_time, content, is_manual_time, created_at, updated_at
        FROM intraday_notes
        WHERE trade_date = ? AND note_time = ?
    ''', (trade_date, candidate_time))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "trade_date": row[1],
        "note_time": row[2],
        "content": row[3],
        "is_manual_time": bool(row[4]),
        "created_at": row[5],
        "updated_at": row[6],
    }


def _get_last_entry(trade_date: str) -> Optional[Dict[str, Any]]:
    """获取指定日期的最后一条记录（按 note_time 排序）"""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, trade_date, note_time, content, is_manual_time, created_at, updated_at
        FROM intraday_notes
        WHERE trade_date = ?
        ORDER BY note_time DESC
        LIMIT 1
    ''', (trade_date,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "trade_date": row[1],
        "note_time": row[2],
        "content": row[3],
        "is_manual_time": bool(row[4]),
        "created_at": row[5],
        "updated_at": row[6],
    }


def _append_paragraph(entry_id: int, new_content: str) -> None:
    """向现有条目追加段落（用 \\n\\n 分隔）"""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT content FROM intraday_notes WHERE id = ?', (entry_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
    existing = row[0] or ""
    merged = f"{existing}\n\n{new_content}" if existing else new_content
    now = _now_iso()
    cursor.execute('''
        UPDATE intraday_notes
        SET content = ?, updated_at = ?
        WHERE id = ?
    ''', (merged, now, entry_id))
    conn.commit()
    conn.close()


def create_note(trade_date: str, content: str,
                note_time: Optional[str] = None,
                prev_time: Optional[str] = None,
                next_time: Optional[str] = None) -> Dict[str, Any]:
    """创建或合并盘中记录

    Args:
        trade_date: 交易日 YYYY-MM-DD
        content: 记录内容
        note_time: 指定时间 HH:MM（None=系统时间）
        prev_time: 插入位置前一条时间（"中间插入"场景）；为 None 表示末尾追加
        next_time: 插入位置后一条时间（"中间插入"场景）；为 None 表示末尾追加

    Returns:
        {"success": bool, "action": "created"|"merged", "id": int, "note_time": str, "msg"?: str}
    """
    if not content or not content.strip():
        return {"success": False, "msg": "内容不能为空"}

    content = content.strip()

    if note_time is not None and note_time != "":
        if not _validate_time(note_time):
            return {"success": False, "msg": f"时间格式错误: {note_time}"}
        is_system = False
        candidate_time = note_time
    else:
        today = _get_query_trading_date()
        in_trading = _is_in_trading_hours()
        is_today_trading = (trade_date == today) and in_trading
        if not is_today_trading:
            return {
                "success": False,
                "msg": "当前不在交易时段，请手动选择时间（{}-{}）".format(TRADING_START, TRADING_END),
                "code": "TIME_REQUIRED",
            }
        is_system = True
        candidate_time = _now_time()

    is_appending = (prev_time is None or prev_time == "") and (next_time is None or next_time == "")

    prev_entry = None
    next_entry = None
    last_input_created_at = None

    if is_appending:
        last_entry = _get_last_entry(trade_date)
        last_input_created_at = last_entry["created_at"] if last_entry else None
    else:
        if prev_time:
            prev_entry = _get_entry_for_merge(trade_date, prev_time)
        if next_time:
            next_entry = _get_entry_for_merge(trade_date, next_time)

    action = resolve_merge_action(
        target_date=trade_date,
        candidate_time=candidate_time,
        prev_entry=prev_entry,
        next_entry=next_entry,
        is_system_time=is_system,
        last_input_created_at=last_input_created_at,
    )

    if action["action"] == "error":
        return {"success": False, "msg": action.get("msg", "时间冲突")}

    if action["action"] == "merge":
        _append_paragraph(action["target_id"], content)
        target_entry = _get_entry_for_merge(trade_date, candidate_time) or _get_entry_by_id(action["target_id"])
        return {
            "success": True,
            "action": "merged",
            "id": action["target_id"],
            "note_time": target_entry["note_time"] if target_entry else candidate_time,
        }

    now = _now_iso()
    conn = _get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO intraday_notes (trade_date, note_time, content, is_manual_time, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (trade_date, candidate_time, content, 0 if is_system else 1, now, now))
        conn.commit()
        new_id = cursor.lastrowid
        return {
            "success": True,
            "action": "created",
            "id": new_id,
            "note_time": candidate_time,
        }
    except sqlite3.IntegrityError:
        existing = _get_entry_for_merge(trade_date, candidate_time)
        if existing:
            _append_paragraph(existing["id"], content)
            return {
                "success": True,
                "action": "merged",
                "id": existing["id"],
                "note_time": existing["note_time"],
            }
        return {"success": False, "msg": "创建失败：UNIQUE 约束冲突但找不到现有记录"}
    finally:
        conn.close()


def _get_entry_by_id(entry_id: int) -> Optional[Dict[str, Any]]:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, trade_date, note_time, content, is_manual_time, created_at, updated_at
        FROM intraday_notes WHERE id = ?
    ''', (entry_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "trade_date": row[1], "note_time": row[2],
        "content": row[3], "is_manual_time": bool(row[4]),
        "created_at": row[5], "updated_at": row[6],
    }


def _validate_time(t: str) -> bool:
    """校验 HH:MM 格式"""
    if not t or len(t) != 5 or t[2] != ":":
        return False
    try:
        h, m = int(t[:2]), int(t[3:])
        return 0 <= h <= 23 and 0 <= m <= 59
    except Exception:
        return False


def update_note(entry_id: int, content: Optional[str] = None,
                note_time: Optional[str] = None) -> Dict[str, Any]:
    """修改条目（内容或时间）

    Args:
        entry_id: 条目 ID
        content: 新内容（None=不改）
        note_time: 新时间 HH:MM（None=不改）

    Returns:
        {"success": bool, "action": "updated"|"merged"|"merged_into_self"|"noop"|"error", "id"?: int, "msg"?: str}
    """
    entry = _get_entry_by_id(entry_id)
    if not entry:
        return {"success": False, "msg": "条目不存在"}

    target_content = content.strip() if content is not None else None
    if content is not None and not target_content:
        return {"success": False, "msg": "内容不能为空"}

    new_time = note_time
    merge_target_id = None

    if new_time is not None and new_time != entry["note_time"]:
        if not _validate_time(new_time):
            return {"success": False, "msg": f"时间格式错误: {new_time}"}

        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, trade_date, note_time, content, is_manual_time, created_at, updated_at
            FROM intraday_notes
            WHERE trade_date = ? AND note_time = ? AND id != ?
        ''', (entry["trade_date"], new_time, entry_id))
        conflict_row = cursor.fetchone()
        conn.close()

        if conflict_row:
            merge_target_id = conflict_row[0]
        else:
            all_entries = list_notes(entry["trade_date"])
            prev_entry = next_entry = None
            for e in all_entries:
                if e["id"] == entry_id:
                    continue
                if e["note_time"] < new_time:
                    prev_entry = e
                elif e["note_time"] > new_time and next_entry is None:
                    next_entry = e

            if prev_entry and new_time <= prev_entry["note_time"]:
                return {"success": False, "msg": f"时间必须晚于上一条 {prev_entry['note_time']}"}
            if next_entry and new_time >= next_entry["note_time"]:
                return {"success": False, "msg": f"时间必须早于下一条 {next_entry['note_time']}"}

    if merge_target_id is not None:
        merge_parts = []
        if target_content is not None:
            merge_parts.append(target_content)
        else:
            merge_parts.append(entry["content"])
        if entry["content"] and target_content is not None and entry["content"] != target_content:
            merge_parts.insert(0, entry["content"])
        merged_content = "\n\n".join([p for p in merge_parts if p])
        _append_paragraph(merge_target_id, merged_content)
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM intraday_notes WHERE id = ?", (entry_id,))
        conn.commit()
        conn.close()
        return {"success": True, "action": "merged", "id": merge_target_id}

    conn = _get_connection()
    cursor = conn.cursor()
    now = _now_iso()
    updates = []
    params = []
    if target_content is not None:
        updates.append("content = ?")
        params.append(target_content)
    if new_time is not None and new_time != entry["note_time"]:
        updates.append("note_time = ?")
        updates.append("is_manual_time = 1")
        params.append(new_time)
    if not updates:
        conn.close()
        return {"success": True, "action": "noop", "id": entry_id}
    updates.append("updated_at = ?")
    params.append(now)
    params.append(entry_id)
    cursor.execute(f"UPDATE intraday_notes SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return {"success": True, "action": "updated", "id": entry_id}


def delete_note(entry_id: int) -> Dict[str, Any]:
    """删除条目"""
    entry = _get_entry_by_id(entry_id)
    if not entry:
        return {"success": False, "msg": "条目不存在"}
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM intraday_notes WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    return {"success": True, "action": "deleted", "id": entry_id}
