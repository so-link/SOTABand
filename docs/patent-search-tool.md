# 专利搜索工具 — 需求分析与设计

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
- API Token 通过【获取Lens API KEY】接口获取

## 实现要点

1. Lens.org API 返回 JSON，解析后提取专利关键信息
2. 用 `requests` 库调用 API，设置 `Authorization: Bearer {token}` 请求头
3. 搜索结果按相关度排序
4. CSV 包含中文列名，UTF-8 编码
5. 如果 Token 未配置，提示用户先注册 Lens.org 账号获取
