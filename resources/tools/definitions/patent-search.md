---
id: patent-search
name: 专利关键词检索
version: 0.2.0
type: function
language: python
status: active
created: 2026-09-30
---

# 专利关键词检索

## 1. 功能概述

用户输入一个关键词，通过 Google Patents 公开接口（无需 API key）做**一次检索**，
返回相关度最高的**前 n 条结果**（标题 / 公开号 / 摘要片段 / 链接）。

**不下载全文、不落盘**，相当于一次简单的 Google Patents 搜索。

可选约束（均输入 `0` 表示不限）：分类号（CPC）、国别、年份范围。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| keyword | string | 是 | - | 检索关键词（覆盖标题+摘要），多个词用空格分隔 |
| ipc | string | 是 | - | 分类号（CPC），输入 `0` 表示不限 |
| country | string | 是 | CN | 国别（CN/US/EP/WO），输入 `0` 表示不限 |
| date_from | string | 是 | - | 起始年份，输入 `0` 表示不限 |
| date_to | string | 是 | - | 截止年份，输入 `0` 表示不限 |
| n | int | 是 | 10 | 返回的结果数量（按相关度取前 n 篇，1~50） |

## 3. 输出规范

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 检索结果摘要（前 n 条标题 + 公开号 + 链接） |
| output_format | string | text |
| data.total | int | 总命中篇数 |
| data.results | list | 结果列表，每项含 publication_number / title / snippet / assignee / url |

## 4. 依赖环境

| 依赖 | 用途 | 必需 |
|------|------|------|
| playwright | Google Patents 浏览器通道 | 是 |

> 本工具不调用大模型，仅做关键词检索 + 返回结果。

## 5. 运行机制

1. 拼接检索式：`keyword` 作为关键词，`CPC=<分类号>` 做分类号限定，年份转 `after=/before=`。
2. 调 `_gp_search` 做一次检索（Playwright 浏览器通道，绕过反爬 503）。
3. 取前 n 条结果直接返回，不下载全文、不落盘。

## 6. 典型用法

- 「搜固态电池液冷，中国，2018 年后，返回前 10 条」
  → `keyword=固态电池 液冷, country=CN, date_from=2018, n=10`

## 7. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-09-30 | 初始版本：模糊/精确检索 + 下载前 N 篇全文到指定目录 |
| 0.2.0 | 2026-10-05 | 简化为一次关键词搜索，删除下载落盘功能，返回前 n 条结果 |
