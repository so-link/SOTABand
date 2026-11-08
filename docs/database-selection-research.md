# SOTABand 数据库与向量检索选型调研

> 调研日期：2026-08 ｜ 触发需求：8.1 用户注册与登录、8.2 用户组网 ｜ 状态：已评审建议

---

## 0. 结论速览

| 问题 | 结论 |
|---|---|
| 项目现在有数据库吗？ | **没有**。全部持久化靠 4 个 JSON registry + Markdown/YAML 定义 + 文件系统（核实过程见第 1 节） |
| 主库选什么？ | **PostgreSQL 16+**（SQLAlchemy 2.0 async + Alembic 迁移）；本地开发可用零配置 SQLite 兜底，表结构保持一致 |
| 有向量数据库场景吗？ | **有真实场景**（工具/Agent 语义发现等），但当前规模**不引入专用向量库**，用同一 PostgreSQL 的 **pgvector** 扩展在 Phase 5 按需启用 |
| Redis / 消息队列？ | 暂不引入。等会话规模、撤权延迟、异步通知有真实证据后再上（Phase 5） |

---

## 1. 现状核实：项目确实没有数据库

逐项排查结果：

- **依赖清单**（pyproject.toml）：仅 fastapi / uvicorn / openai / sse-starlette / pydantic / pyyaml / python-multipart，**无任何数据库驱动或 ORM**；
- **代码 grep**：全库无 sqlalchemy / psycopg / asyncpg / pymysql / sqlmodel / create_engine / SessionLocal / Base.metadata。仅有的两处 sqlite3 出现在 scripts/check_tool_env.py 与 core/resource/builder/tool_builder.py 的 **Python 标准库白名单**里，用途是工具环境依赖检查，与数据库无关；
- **文件层面**：无 .db / .sqlite3 文件、无 .sql 迁移脚本（.gitignore 中的 *.db 规则只是预防性配置）；
- **配置层面**：config/settings.py 的 StorageConfig 声明了默认 sqlite:///sotaband.db，但**全库没有任何代码引用该配置**，属于预留未实现；
- **实际持久化**：core/api/registry.json、resources/{agents,data,tools}/registry.json 四个 JSON 注册表 + 定义 Markdown/YAML + 本地文件（resources/data/datasets、uploaded_images）；storage/ 与 core/observability/ 目录均为 docstring 空壳。
0
**结论：目前项目运行在「JSON 注册表 + 文件系统」上，没有数据库层。** 这正印证 PRD 的判断：JSON registry 无法表达成员关系、唯一约束、并发邀请、权限查询与审计，8.1/8.2 落地必须引入真正的数据库。

---

## 2. 未来数据形态分析

| 数据类别 | 例子 | 特征 | 存储选择 |
|---|---|---|---|
| 强关系数据 | users、sessions、tenants、networks、memberships、invitations、publications | 外键、联合唯一约束、事务、并发、软删除、审计 | **关系型数据库（唯一正确选择）** |
| 资源元数据 | Agent/Tool/Data 的 id、版本、tags、spec 文档 | 半结构化，spec 本体是 Markdown/YAML | PostgreSQL JSONB 存结构化字段，源文档继续留文件系统/Git 做版本控制 |
| 大文件/数据集本体 | CSV、图片、模型 .pkl、上传文件 | 体积大、不可查询 | 文件系统（data/files），数据库只存元数据与路径 |
| 审计日志 | audit_events | 只追加、量大、按时间查询 | PostgreSQL 分区表（MVP 单表即可，后期按月份分区） |
| 会话/权限缓存 | session token hash、membership 缓存 | 短期、高频、要求快速吊销 | MVP 直接用 PostgreSQL；规模上来后引入 Redis（Phase 5） |

---

## 3. 候选数据库对比

| 维度 | PostgreSQL 16+ | MySQL 8 | SQLite | MongoDB |
|---|---|---|---|---|
| 关系完整性（FK/约束/事务） | 最强 | 强 | 中 | 弱 |
| 多租户行级安全（RLS） | 原生支持 | 无 | 无 | 弱 |
| 半结构化 JSON | JSONB 可索引 | JSON 可索引 | JSON1 | 强但无强约束 |
| 向量检索 | pgvector（成熟、同库混合查询） | 仅云版 HeatWave | sqlite-vec（早期） | 仅 Atlas 云版 |
| 部署成本 | 需安装/Docker | 需安装 | 零配置 | 需安装 |
| FastAPI 生态契合度 | 社区默认 | 良好 | 良好 | 一般 |

**推荐：PostgreSQL 16+ 为唯一主库选择**，理由：

1. 8.1/8.2 的成员关系、邀请幂等、最后 Owner 保护、撤权审计全部依赖**强约束 + 事务**，这是关系库的主场；
2. **RLS** 为多租户隔离提供纵深防御（应用层授权仍是主防线）；
3. **JSONB** 无缝承接现有 registry 的资源元数据，迁移成本最低；
4. **pgvector** 让向量检索零新增组件（见第 4 节）；
5. 本地开发用 SQLite 兜底（零配置跑通测试），但 schema 由 Alembic 统一管理，避免两种库行为漂移。

---

## 4. 向量数据库场景分析

### 4.1 现状：零向量使用，但已有 naive 场景

- 全库（前后端）grep 无 embedding / vector / faiss / chroma / milvus / qdrant / pgvector 任何痕迹；
- 现有 app/api/routes/data_routes.py 的 match-tools 接口实现方式是：**把所有 active 工具的元数据拼进 LLM prompt，让模型挑选**。工具少时可行，但存在三个问题：token 成本随工具数线性增长、无召回排序分数、受上下文长度硬限制。

### 4.2 真实向量场景（按价值排序）

| 优先级 | 场景 | 说明 |
|---|---|---|
| P1 | **工具/Agent 语义发现** | 本项目核心理念「描述即执行」：用户用自然语言描述需求 → 找最相关的现有工具/Agent。做法：给每个工具 spec 生成 embedding，需求 embedding → 相似度 Top-K → 交给 LLM 精排。现有 match-tools 正是此场景的 LLM 全量版 |
| P2 | 数据集语义检索 | 按描述找数据集（现有代餐奶昔数据、ETTh1 等均有描述文档） |
| P3 | Agent 记忆与 RAG | 对话历史检索；论文类工具（paper-summarizer、arxiv-paper-downloader）的文档分块检索 |
| P4（远期） | 多模态检索 | 图片工具链（face-detection、pose、image-generation）未来可用 CLIP 实现以图搜图 |

### 4.3 为什么不直接上专用向量库（Milvus/Qdrant/Chroma）

- **量级不匹配**：当前几十个工具/Agent 定义、几百条数据，专用向量库的部署、数据同步、一致性维护成本远超收益；
- **一致性**：元数据与向量需要在同一事务里更新（发布新版本工具 = 更新定义 + 更新向量），pgvector 天然同库同事务；Qdrant 官方博客也承认 pgvector 的同步问题是「真实存在但可解决」，为小规模项目自己再解决一遍不值得；
- **多租户混合查询**：8.1/8.2 后所有检索都要叠加 tenant/network scope 过滤，pgvector 直接在 SQL 里 WHERE tenant_id + ORDER BY distance 最自然，专用向量库要做额外的 payload 过滤与两系统数据同步；
- **升级路径清晰**：pgvector 在百万级向量内（HNSW 索引）性能足够；真到数百万级向量、需要独立扩展召回集群时，把 repository 层替换为 Qdrant 即可，表结构不用改。

### 4.4 引入时机与方式

- **触发条件**（Phase 5 评估）：match-tools 出现 token 成本或召回质量问题，或正式立项「语义发现」功能；
- **引入方式**：PostgreSQL 16 + pgvector 扩展；表预留 embedding 列（vector 类型）；
- **Embedding 模型**：留作 Phase 5 调研项——DeepSeek 当前不提供 embedding 接口，候选有 OpenAI text-embedding-3-small、自部署 bge-m3、国内可访问的 embedding 服务，届时按「国内可达性 + 成本 + 中文效果」选型；
- **数据流**：spec 文档 → 分块 → embedding → 向量列；检索：query embedding → Top-K + tenant/network 过滤 → LLM 重排返回。

---

## 5. 与 8.1/8.2 落地的绑定

- 在 PostgreSQL 上建 PRD 第 7.2 节的 11 张核心表；
- 技术栈：SQLAlchemy 2.0 async + Alembic 迁移 + psycopg3；
- GitHub 登录映射：user_identities（issuer=github，subject=GitHub 用户数字 ID），详见 PRD FR-A1/A2 更新；
- audit_events 按时间分区；
- Redis、消息队列（outbox 通知）、向量检索均挂到 Phase 5 决策门，不提前引入。

---

## 6. 参考

- [GitHub OAuth App 授权文档](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)
- [GitHub OAuth 与 GitHub App 认证 PKCE 支持公告（2025-07-14）](https://github.blog/changelog/2025-07-14-pkce-support-for-oauth-and-github-app-authentication/)
- [pgvector](https://github.com/pgvector/pgvector)
- [Qdrant：pgvector 的取舍](https://qdrant.tech/blog/pgvector-tradeoffs/)
