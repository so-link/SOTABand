---
id: image-collector
name: 图片采集器
version: 0.1.0
type: function
language: python
status: active
created: 2026-07-26
---

# 图片采集器

## 1. 功能概述
根据用户需求关键词，从 Unsplash 搜索并下载指定数量的图片到本地时间戳目录；随后通过【数据集注册API】将该目录注册为图片数据集，并返回第一张下载图片的本地路径。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| req | string | 是 | - | 图片搜索关键词，如 "city night" |
| n | integer | 是 | - | 需要下载的图片数量 |
| dataset | string | 是 | - | 注册到系统的数据集名称 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明 |
| output_format | string | image |
| data | dict | `{"image_path":"/path/to/first_image.jpg"}` |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `image` | `{"image_path":"/path/to/file.jpg"}` | 直接绘制图片 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | ≥3.8 | 运行环境 |
| requests | ≥2.28 | 发送 HTTP 请求 |
| tqdm | ≥4.64 | 进度条显示 |
| os | 内置 | 文件路径处理 |
| re | 内置 | 正则表达式 |

## 5. 运行机制

### 5.1 执行流程
1. 生成格式为 `{时间戳}` 的子目录名，创建本地路径 `./data/download/{时间戳}/`。
2. 构造搜索 URL：`https://unsplash.com/napi/search/photos?query={req}&per_page=30`。
3. 使用 `requests` 发起请求，解析 JSON 数据，提取图片地址，依次下载前 `n` 张图片到步骤 1 创建的目录中。
4. 调用【数据集注册API】将 `./data/download/{时间戳}/` 注册为数据集 `{dataset}`。
5. 返回下载的第一张图片的本地绝对路径。

### 5.2 错误处理
- 目录创建失败 → 返回错误信息，终止执行。
- Unsplash API 请求失败或返回非 200 状态 → 捕获异常，返回“搜索请求失败”。
- 图片下载异常（超时、链接失效等）→ 跳过当前图片，继续下一个，若下载成功数为 0 则返回“无图片下载成功”。
- 数据集注册 API 调用失败 → 返回注册阶段错误信息。
- 输入参数 `n` 小于 1 → 返回参数验证错误。

### 5.3 参考代码
```python
通过requests在下面网站进行搜索："https://unsplash.com/napi/search/photos?query={req}&per_page=30" ，搜索相关图片的具体代码参考：

import requests
import os
from tqdm import tqdm
import re

SAVE_DIR = "online photo"
os.makedirs(SAVE_DIR, exist_ok=True)

queries = []
with open("input.txt", "r", encoding="utf-8") as f:
    for line in f:
        if line.startswith("场景"):
            queries = re.findall(r'[\"“](.*?)[\"”]', line)
# queries = [
#     "drone skyscraper city",
#     "aerial city skyline",
#     "drone aerial buildings",
#     "drone view downtown",
# ]

headers = {
    "User-Agent": "Mozilla/5.0"
}

def download(url, idx):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open(f"{SAVE_DIR}/{idx}.jpg", "wb") as f:
                f.write(r.content)
    except:
        pass


def crawl_unsplash(query, start_idx):

    url = f"https://unsplash.com/napi/search/photos?query={query}&per_page=30"

    r = requests.get(url, headers=headers)
    data = r.json()

    idx = start_idx

    for img in data["results"]:

        img_url = img["urls"]["regular"]

        download(img_url, idx)

        idx += 1

    return idx


idx = 0

for q in queries:
    print("Searching:", q)
    idx = crawl_unsplash(q, idx)

print("done")
```

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-07-26 | 初始版本 |