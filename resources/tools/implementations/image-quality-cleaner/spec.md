---
id: image-quality-cleaner
name: 图像数据清洗
version: 0.1.0
type: script
language: python
status: active
created: 2026-11-20
---

# 图像数据清洗

## 1. 功能概述

对已注册的图片数据集做规则化质量清洗，输出可直接用于 YOLO标注训练的数据集：图像统一编号命名（`images/000001.jpg`），若源目录存在同名 YOLO 标签则同步搬运并编号（`labels/000001.txt`），全部剔除决定写入 `clean_report.json` 可追溯。

设计定位：图像数据进入训练管线前的第一道工序。与「大模型批量标框」（`batch-ai-bbox-labeling`）串成 **标框 → 清洗 → 训练** 链路——标框产出的带标签数据集可直接作为本工具输入。

清洗规则均为成熟做法，阈值保守且全部可覆盖；规则只做筛选与归一化，不改动图像内容语义（默认不缩放）。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| dataset | string | 是 | - | 源图片数据集名称，系统通过【获取数据集信息】解析其实际路径；只扫描根目录，不扫描 `labeled/` 等子目录 |
| output_dataset | string | 是 | - | 清洗后数据集名称，通过【数据集注册API】注册 |
| min_size | int | 否 | 64 | 图片宽高下限（像素），小于该值直接剔除；有效范围 16–4096 |
| min_brightness | float | 否 | 35 | 灰度亮度均值下限，低于判为过暗剔除 |
| max_brightness | float | 否 | 215 | 灰度亮度均值上限，高于判为过亮剔除 |
| min_contrast | float | 否 | 20 | 灰度标准差下限，低于判为纯色/低信息量剔除 |
| min_edge | float | 否 | 6 | FIND_EDGES 边缘强度均值下限，低于判为模糊剔除 |
| dedupe | bool | 否 | true | 是否启用精确去重（文件字节 MD5），`false`/`0`/`no` 可关闭 |

阈值经 test03 数据集（86 正常图 + 25 坏图）实测标定：好图亮度 42.8–202.8 / 边缘 7.9–78.6 / 对比度 ≥29.6；坏图分别为 0–26 或 224–255、边缘 ≤6.9、对比度 ≤13.6。不启用 dHash 近似去重——实测对同场景不同图误杀率 8/86。
| normalize_exif | bool | 否 | true | 是否按 EXIF 方向旋转归一 |
| max_edge | int | 否 | 0 | 最长边上限（像素），0 表示不缩放；大于 0 时等比缩放 |

## 3. 输出规范

### 3.1 标准输出字段

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | `success` 或 `failed` |
| message | string | 结果说明，如 "清洗完成：100 张输入，保留 87 张，剔除 13 张" |
| output_format | string | 固定为 `image` |
| data | dict | 详见 3.2 |

### 3.2 data 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| image_path | string | 第一张保留图片路径，供界面预览 |
| dataset_id | string | 注册后的数据集 id |
| data_path | string | 产物根目录 |
| report_path | string | `clean_report.json` 路径，含逐张的保留/剔除原因与规则快照 |
| total / kept / rejected | int | 输入 / 保留 / 剔除数量 |
| label_count | int | 成功同步的 YOLO 标签数量 |

### 3.3 产物目录结构

```
<USER_HOME>/data/downloads/cleaned_<timestamp>/
├── dataset/                       ← 注册为清洗后数据集的目录（data_path 指向这里）
│   ├── images/                    000001.jpg, 000002.jpg ...  仅保留的高质量图像
│   ├── labels/                    000001.txt, 000002.txt ...  YOLO 标签
│   └── clean_report.json          规则快照 + 逐张保留/剔除原因
└── rejected/                      ← 被剔除的原图留档，供人工复查（不在数据集目录内）
                                      BAD_*.jpg 等，前端预览不会显示
```

被剔除的图只留在 `rejected/`，**不进入注册数据集**——前端预览只看到保留图。
每张被剔除图的理由记在 `dataset/clean_report.json` 的 `rejected_items` 里。

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| Pillow | 13.0.0 | 图像读取、EXIF 归一、灰度统计、边缘检测、JPEG 保存 |

仅使用 Python 标准库与 Pillow，不调用外部 LLM，端点与密钥不经工具进程。清洗规则为确定性规则，无随机性。

## 5. 运行机制

### 5.1 执行流程

1. 校验 `dataset` / `output_dataset` 必填参数，解析并规范化清洗阈值。
2. 经系统 API【获取数据集信息】（`api-data-get`）取得源数据集路径，只扫描根目录下的图片文件（`.jpg/.jpeg/.png/.bmp/.tiff/.webp`），忽略 `labeled/` 等子目录。
3. 在 `<USER_HOME>/data/downloads/cleaned_<timestamp>/` 下建立 `images/`、`labels/`、`rejected/` 三个子目录。
4. 逐张执行清洗判定，按顺序应用以下规则：
   1. **EXIF 归一**：按 EXIF 方向旋转，避免训练时图像朝向不一致；
   2. **模式归一**：RGBA/P/灰度统一转 RGB；
   3. **尺寸下限**：宽或高小于 `min_size` → 剔除；
   4. **亮度异常**：灰度均值低于 `min_brightness` 或高于 `max_brightness` → 剔除；
   5. **低信息量**：灰度标准差低于 `min_contrast` → 剔除（纯色/近纯色图）；
   6. **模糊检测**：`FIND_EDGES` 滤波后灰度均值低于 `min_edge` → 剔除（高频分量过少）；
   7. **精确去重**：文件字节 MD5 与已保留图一致 → 剔除，先到先保留（不做近似去重，避免误删同场景不同图）；
   8. **可选缩放**：`max_edge > 0` 且最长边超限 → 等比缩放（默认关闭，保护原始分辨率）。
5. 通过判定的图像以 6 位序号命名保存为 JPEG；若源目录存在同名 `.txt` 标签，同步搬运为对应序号的 `.txt`。
6. 未通过判定的图像复制原文件到 `rejected/` 留档，并在报告中记录文件名与剔除原因。
7. 写出 `clean_report.json`（含规则快照、逐张保留/剔除记录）。
8. 经系统 API【数据集注册API】（`api-data-register`）将产物目录注册为 `output_dataset`，数据集描述记录源数据集与保留比例。
9. 返回第一张保留图路径、数据集 id、统计数量与报告路径。

### 5.2 错误处理

- 缺少 `dataset` / `output_dataset` → `status: failed`，`"缺少必要参数 dataset, output_dataset"`。
- 数据集不存在、路径不可访问或目录内无图片 → `status: failed`，`"数据集不存在或无法访问"` / `"数据集中没有找到图片文件"`。
- 全部图片均未通过清洗 → `status: failed`，`"全部 N 张图片均未通过清洗，详见报告"`。
- 数据集注册失败 → `status: failed`，返回 `"数据集注册失败: {错误详情}"`。
- 单张图片读取/处理异常 → 记入 `rejected` 并继续处理其余图片，不中断整批。

### 5.3 性能与阈值建议

- 规则为 CPU 计算，无网络调用；处理速度取决于图片数量与尺寸。
- 默认阈值偏保守（只滤明显坏图），如需严格筛选可调高 `min_size` / `min_contrast` / `min_edge`。
- 去重按文件字节 MD5，内容完全一致的图才会被剔除；视觉相似但文件不同的图都保留，由训练侧决定是否进一步筛选。

## 6. 测试用例

### 6.1 正常场景

输入数据集包含 100 张图，其中 5 张尺寸过小、3 张纯色、2 张模糊、4 张近似重复：
预期返回 `kept=86`、`rejected=14`，`images/` 下有 86 个连续编号的 jpg，报告里每张被剔除图都有明确原因。

### 6.2 边界条件

- 输入只有 1 张图且通过清洗 → 正常注册，`image_path` 指向 `000001.jpg`；
- 所有图都不合格 → `status: failed`，但 `rejected/` 与报告仍生成供复查；
- 源目录无同名 `.txt` → `labels/` 为空，`label_count=0`，不阻塞清洗；
- 图片带 EXIF 旋转信息 → 按方向归一后再做质量判定与保存；
- `min_size` 传 0 或 10000 → 自动钳制到 16 / 4096。

## 7. 调用示例

```python
result = execute(
    dataset="raw_images",
    output_dataset="cleaned_images",
    min_size=128,
    dedupe=True,
)
# result = {
#     "status": "success",
#     "message": "清洗完成：100 张输入，保留 86 张，剔除 14 张",
#     "output_format": "image",
#     "data": {
#         "image_path": "/root/SB-USER/1000/data/downloads/cleaned_20261120120000/images/000001.jpg",
#         "dataset_id": "cleaned_images_20261120120000",
#         "total": 100, "kept": 86, "rejected": 14, "label_count": 86,
#         "report_path": ".../clean_report.json",
#         "data_path": ".../cleaned_20261120120000"
#     }
# }
```

## 8. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-11-20 | 初始版本：规则化图像清洗、感知哈希去重、YOLO 标签同步、清洗报告与数据集注册 |
