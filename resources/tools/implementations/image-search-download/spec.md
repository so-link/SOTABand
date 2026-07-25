---
id: image-search-download
name: 图片搜索与下载工具
version: 0.1.0
type: script
language: python
status: active
created: 2025-04-01
---

# 图片搜索与下载工具

## 1. 功能概述

根据用户输入的搜索需求，从 Bing 搜索引擎获取相关图片，下载指定数量的图片到本地时间戳子目录，并通过【数据集注册API】将目录注册为图片数据集，最后返回下载的第一张图片。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| req | string | 是 | 无 | 图片搜索关键词 |
| n | integer | 是 | 无 | 需要下载的图片数量 |
| dataset | string | 是 | 无 | 数据集名称，用于注册到平台 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | image |
| data | dict | 包含 image_path 字段，指向下载的第一张图片路径 |

示例：
```json
{
  "status": "success",
  "message": "成功下载并注册数据集",
  "output_format": "image",
  "data": {
    "image_path": "./data/download/1711939200/result_1.jpg"
  }
}
```

### 3.2 可视化输出格式
本工具固定输出 `image` 格式，`data` 中提供 `image_path`，前端将直接渲染该图片。

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| bing-image-downloader | >=1.1.0 | Bing 图片搜索与下载 |
| shutil | 标准库 | 文件操作 |
| os | 标准库 | 路径处理 |
| datetime | 标准库 | 时间戳生成 |
| 系统API：【数据集注册API】 | - | 注册图片数据集 |

## 5. 运行机制

### 5.1 执行流程
1. 生成当前时间戳 `{xxxx}`，创建子目录 `./data/download/{xxxx}/`
2. 调用 `bing-image-downloader` 库，根据 `{req}` 搜索图片，下载前 `{n}` 张至上述目录
3. 调用【数据集注册API】，将目录 `./data/download/{xxxx}/` 注册为名为 `{dataset}` 的图片数据集
4. 读取下载目录中的第一张图片，组装返回结果

### 5.2 错误处理
- 搜索无结果 → 返回 `status: failed`，并提示无相关图片
- 下载失败或数量不足 → 返回实际下载情况，并标记 `failed`（若一张都未成功）
- 目录创建失败 → 返回错误信息
- 【数据集注册API】调用异常 → 返回注册失败错误

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2025-04-01 | 初始版本 |