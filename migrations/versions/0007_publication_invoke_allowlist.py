"""publication invoke allowlist (FR-N7 按目标粒度授权)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-27 20:40:00.000000

归属需求：8.2 用户组网 —— FR-N7「Agent 之间可相互通信」
设计依据：docs/design-08-02-network.md §8.3

## 这次迁移补的是什么

批次 5 的白名单是**粗粒度**的：目标只要发布到了本网络，网络里任何有
`network.agent.invoke` 权限的成员都能调。设计文档当时如实记下了这个简化。

本次补上"按目标"的那一半：发布者可以给自己的资源设置
- `network`（默认）：网络内所有有权限的成员都能调
- `allowlist`：只有名单里的成员能调

## 为什么是两列而不是一张新表

白名单规模很小（一个网络几十个成员），且**只会整体读写**——没有"单独查某个
白名单项"的查询需求。为它开一张表意味着多一次 join、多一套 CRUD 和迁移，
收益不抵成本。JSON 数组在这里是合适的选择。

代价：无法按白名单项建索引。但这张表的访问路径永远是
"按 (resource_type, release_id, scope, scope_id) 找到那一行"，已经走了唯一索引，
拿到行之后在内存里做 `in` 判断，没有性能问题。

## 为什么这两列不加 CHECK 约束

SQLite 的 `ALTER TABLE` 不支持新增表级 CHECK（要重建表）。加了会让
"`create_all` 建的库" 与 "迁移建的库" 结构不一致，而 alembic autogenerate
检测不到 CHECK，这种漂移会长期潜伏不报错。

这两个字段只有 `set_publication_invoke_policy()` 一个写入口，合法性（策略值、
名单非空、成员确实在该网络）全部在服务层校验。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "publication",
        sa.Column(
            "invoke_policy",
            sa.String(length=16),
            nullable=False,
            server_default="network",
        ),
    )
    op.add_column(
        "publication",
        sa.Column("invoke_allowlist", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publication", "invoke_allowlist")
    op.drop_column("publication", "invoke_policy")
