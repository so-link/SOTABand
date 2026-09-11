---
id: cvpr-paper-downloader
name: CVPR顶会论文下载
version: 0.1.0
type: script
language: python
status: active
created: 2026-09-08
---

# CVPR顶会论文下载

## 1. 功能概述

根据用户需求，通过 CVF Open Access 网站检索计算机视觉顶会 CVPR、ICCV 论文，筛选发表时间在指定年份及之后的论文，按相关程度排序后下载前 n 篇论文 PDF，并将论文基本信息整理为 Markdown 文件保存到指定目录。最终返回已下载论文的列表。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| req | string | 是 | 无 | 用户需求或检索关键词 |
| n | int | 是 | 无 | 需要下载的论文数量，按相关程度排序取前 n 篇 |
| year | int | 是 | 无 | 发表年限阈值，仅下载 year 年及以后发表的论文 |
| data_path | string | 是 | 无 | 论文保存目录，PDF 文件和 Markdown 汇总文件均保存到此目录 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | table |
| data | dict | 包含 columns、rows、md_file |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `table` | `{"columns":["序号","标题","会议","年份","论文链接","本地PDF路径"], "rows":[[1,"Paper Title","CVPR",2024,"https://...","/data/paper1.pdf"], ...], "md_file":"/data/paper_list.md"}` | 渲染表格 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| requests | >=2.31.0 | 访问 CVF Open Access 网页、下载论文 PDF |
| beautifulsoup4 | >=4.12.0 | 解析论文列表 HTML |
| lxml | >=4.9.0 | BeautifulSoup 解析后端 |
| tqdm | >=4.66.0 | 下载进度显示（可选） |

## 5. 运行机制

### 5.1 执行流程
1. 校验输入参数 req、n、year、data_path
2. 根据年份和会议类型构建 CVF Open Access 论文列表 URL
3. 获取论文列表，按标题和摘要与 req 的相关程度排序
4. 筛选发表年份大于等于 year 的论文，取前 n 篇
5. 解析每篇论文详情页，获取 PDF 下载链接
6. 下载 PDF 到 data_path
7. 收集论文基本信息，生成 Markdown 汇总文件保存到 data_path
8. 返回下载论文列表

### 5.2 错误处理
- 网站不可访问或请求超时 → 返回 `status: failed`，错误信息：无法访问 CVF Open Access 网站
- 未找到符合年份或相关度条件的论文 → 返回 `status: failed`，错误信息：未找到符合条件的论文
- 参数无效，如 `n <= 0`、`year` 非数字、`data_path` 不可写 → 返回验证错误
- 部分论文 PDF 下载失败 → 继续下载其他论文，并在 message 中说明失败数量
- 处理异常 → 捕获并返回详细错误信息

### 5.3 参考代码

```python
https://openaccess.thecvf.com/ 网站上的CVPR论文列表的网址如下：https://openaccess.thecvf.com/CVPRXXXX?day=all，其中XXXX是具体的年份。
ICCV论文列表的网址如下：
https://openaccess.thecvf.com/ICCVXXXX?day=all，其中XXXX是具体的年份。
可以访问对应的网页，获得对应一年的文章的列表，其中每篇论文都有下载链接
```

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-09-08 | 初始版本 |