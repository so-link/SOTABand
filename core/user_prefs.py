"""个人偏好存储（落在**当前用户自己的** platform.db）

设计动机：主题、三栏宽度、左栏标签、打开的标签页、对话确认策略……这些状态
此前全在浏览器 localStorage。多用户之后这不再成立——同一人在两台设备登录
同一个账号，界面应当一致；更重要的是「谁改的」需要能落到谁的目录里，而不是
留在某台机器的浏览器缓存。

key 命名用前端原 localStorage key（如 ``ui.theme``、``layout.widths``），
前端只做一层"本地缓存 + 远端权威"的同步，改动面最小。

值统一 JSON 序列化存 TEXT：偏好项天然是异构结构（数字、布尔、对象），
一项一列会在每次新增偏好时改表。
"""

from __future__ import annotations

import json
import time
from typing import Any

from core.user_db import UserDbPool

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_prefs (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

_pool = UserDbPool(_SCHEMA)

# 单次全量写入的键数上限（偏好项是有限集合；超限说明前端在拿它当数据仓）
MAX_KEYS = 200
# 单个值序列化后的字符上限，防止把大对象塞进偏好表
VALUE_MAX_CHARS = 16_000


def _lock():
    return _pool.uid_lock()


def _db():
    return _pool.get()


def get_all() -> dict[str, Any]:
    """当前用户的全部偏好（解析后的值）。坏行跳过而不是整体报错。"""
    with _lock():
        rows = _db().execute("SELECT key, value_json FROM user_prefs").fetchall()
    out: dict[str, Any] = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value_json"])
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def set_many(values: dict[str, Any]) -> dict[str, Any]:
    """整组写入（覆盖同名 key）。返回实际写入的键。

    这里不做"删除未传入的 key"——前端按模块增量同步，全量替换会让某个
    组件的保存动作抹掉其它组件的偏好。删除单个 key 走 delete()。
    """
    if not isinstance(values, dict):
        raise ValueError("偏好必须是键值对象")
    if len(values) > MAX_KEYS:
        raise ValueError(f"偏好项过多（上限 {MAX_KEYS}）")
    import time

    payload: list[tuple[str, str, float]] = []
    for key, val in values.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ValueError(f"偏好 key 非法: {key!r}")
        text = json.dumps(val, ensure_ascii=False)
        if len(text) > VALUE_MAX_CHARS:
            raise ValueError(f"偏好 {key} 过大（上限 {VALUE_MAX_CHARS} 字符）")
        payload.append((key, text, time.time()))
    with _lock():
        conn = _db()
        for key, text, now in payload:
            conn.execute(
                "INSERT INTO user_prefs (key, value_json, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value_json=?, updated_at=?",
                (key, text, now, text, now),
            )
        conn.commit()
    return {"saved": [p[0] for p in payload]}


def delete(key: str) -> None:
    with _lock():
        _db().execute("DELETE FROM user_prefs WHERE key=?", (key,))
        _db().commit()
