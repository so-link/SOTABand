# 专利搜索工具 — 需求分析与设计

> 状态：本需求已通过工具 **`lens-patent-downloader`（Lens专利检索与注册工具）** 落地（另有关联工具 `patent-translation-tool` 专利检索与翻译工具），实际实现与本设计存在以下差异：
> 1. **输入参数**为 `req`（关键词/自然语言描述）、`n`（数量）、`year`（公开年份下限）、`dataset`（数据集名）四个必填参数；发明人、申请人、IPC 分类号、日期范围筛选**尚未实现**，为后续扩展方向；
> 2. **输出**不只是一份 CSV：每篇专利经 `_llm_chat` 翻译成中文后保存为独立 Markdown 文件（标题/摘要/权利要求/说明书），并汇总生成 `patents_info.csv`（字段：专利号、标题、公开日期、发明人、摘要）；设计中的**被引用次数、法律状态字段未包含**；
> 3. **输出目录**为 `data/download/`（而非 `./data/patents/`），随后通过 `api-data-register` 注册为数据集；
> 4. **Token 获取**：系统 API 中**没有**「获取 Lens API KEY」接口（现有密钥类 API 仅覆盖豆包/DeepSeek）；Token 通过环境变量 `LENS_API_KEY`（在 `.env` 中配置）注入，工具内置的历史兜底值应尽快到 Lens.org 轮换。
>
> 以下保留原始需求分析与设计作为扩展方向参考。

## 需求描述

输入：搜索关键词{query}，最大数量{n}，数据集名称{dataset}
过程：
1）根据{query}，通过 Lens.org 免费 API（https://www.lens.org/）检索全球专利
2）支持按关键词、发明人{inventor}、申请人{applicant}、IPC分类号{ipc}、日期范围（{date_from} 到 {date_to}）筛选
3）返回前{n}条最相关专利（默认50条）
4）提取关键字段：专利号、标题、申请人、发明人、公开日期、IPC分类、被引用次数、法律状态
5）结果保存为 CSV 文件到目录 `./data/patents/`
6）通过【数据集注册API】将 CSV 文件目录注册为数据集{dataset}

## 工具设计

### 基本信息

- **ID**: `patent-searcher`
- **名称**: 专利搜索工具
- **类型**: function
- **标签**: `["专利搜索", "文献检索", "知识产权"]`

### 输入参数

| 参数名 | 类型 | 必填 | 默认值 | 描述 |
|--------|------|------|--------|------|
| query | string | 是 | - | 搜索关键词（支持 AND/OR/NOT 布尔运算） |
| n | integer | 否 | 50 | 最大返回数量，上限 200 |
| inventor | string | 否 | - | 发明人姓名 |
| applicant | string | 否 | - | 申请人/专利权人 |
| ipc | string | 否 | - | IPC 分类号（如 G06F） |
| date_from | string | 否 | - | 公开日期起始（YYYY-MM-DD） |
| date_to | string | 否 | - | 公开日期截止（YYYY-MM-DD） |
| dataset | string | 是 | - | 目标数据集名称 |

### 输出

- **格式**: CSV 表格 + 数据集注册
- **字段**: 专利号、标题、摘要、申请人、发明人、公开日期、IPC分类、被引用次数、法律状态

## 数据源

使用 **Lens.org 免费 API**：
- 覆盖全球 1.4 亿+ 专利文献
- 免费注册即用，每日 5000 次请求
- 注册地址：https://www.lens.org/lens/user/subscriptions
- API Token 目前没有对应的「获取 Lens API KEY」系统 API，需在工具运行环境（如 `.env` / 环境变量）中配置（现有密钥类系统 API 仅 `api-doubao-get-key` / `api-deepseek-get-key`）

## 实现要点

1. Lens.org API 返回 JSON，解析后提取专利关键信息
2. 用 `requests` 库调用 API，设置 `Authorization: Bearer {token}` 请求头
3. 搜索结果按相关度排序
4. CSV 包含中文列名，UTF-8 编码
5. 如果 Token 未配置，提示用户先注册 Lens.org 账号获取
