"""注册表 JSON 文件的原子写入。

注册表（registry.json）是各资源空间的唯一真相来源，进程崩溃或并发读写时
半截文件会导致整表不可读。写入统一走「同目录临时文件 + os.replace」：
POSIX 上 replace 是原子操作，读者要么看到旧版本、要么看到新版本。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path: Path | str, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
