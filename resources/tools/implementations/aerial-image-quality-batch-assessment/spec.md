---
id: aerial-image-quality-batch-assessment
name: 航拍图片批量质量评估
version: 0.1.0
type: script
language: python
status: active
created: 2026-08-29
---

# 航拍图片批量质量评估

## 1. 功能概述
对指定数据集中的所有航拍图片，从清晰度、光照、目标遮挡、目标完整性四个维度进行批量质量评估。
通过【获取数据集信息】获取数据集目录，用线程池并发调用大模型逐张看图打分，
最终输出数据集级 JSON 报告文件，并在界面以表格展示明细。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| dataset_name | string | 是 | 无 | 数据集名称，通过【获取数据集信息】获取图片目录 |
| concurrency | integer | 否 | 8 | 并发线程数，控制同时调用大模型的数量。若传入值类型非整数或小于1，则使用默认值8。 |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed |
| message | string | 结果说明或错误信息 |
| output_format | string | 固定为 `table` |
| data | dict | 见 3.2 |

### 3.2 可视化输出格式
output_format 为 `table`，data 结构如下：
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `table` | `{"columns": ["文件名","综合分","清晰度","光照","遮挡","完整性","质量标签","问题描述"], "rows": [[...]], "report_path": "/abs/path/quality_report.json"}` | 渲染表格，同时提供 JSON 报告下载 |

rows 取综合分升序的前 20 条。

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| Pillow | >= 9.0 | 图片读取、等比缩放、JPEG 编码 |

禁止使用 pip install，依赖由系统自动管理；禁止使用 asyncio / async / await。

## 5. 运行机制

### 5.1 执行流程
1. 调用【获取数据集信息】，传入 name=dataset_name，取返回 dict 中的 `data_path` 作为图片目录。
2. 遍历该目录下所有 .jpg 文件（按文件名排序）。列表为空则返回 failed。
3. 用 concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) 并发处理。
   每个线程内对单张图片执行 a～d：
   a. 用 PIL 打开图片，等比缩放到最长边不超过 1024 像素，
      保存为 JPEG(quality=85) 到内存缓冲区，再 base64 编码为字符串。
   b. 调用大模型（必须使用工具模板提供的 _llm_chat 函数，禁止直连任何服务商端点、
      禁止硬编码模型名），消息格式：
      messages = [
        {"role": "user", "content": [
          {"type": "image_url",
           "image_url": {"url": "data:image/jpeg;base64,{base64字符串}"}},
          {"type": "text", "text": 提示词}
        ]}
      ]
      提示词全文如下：
      """你是航拍图像质量评估专家。请为这张航拍图片打分。
      
      【评分规则】四个维度均为 0-100 分，分数越高代表质量越好：
      
      1. clarity（画面清晰度）：
         - 90-100 细节锐利，边缘清晰可辨
         - 70-89  整体清晰，个别区域略软
         - 40-69  明显模糊，细节有损失
         - 0-39   严重模糊，内容难以辨认
      
      2. light（光照质量）：曝光是否合适
         - 90-100 曝光准确，明暗细节完整
         - 70-89  基本正常，轻微偏亮或偏暗
         - 40-69  明显过曝（发白、高光溢出）或明显欠曝（发黑、暗部无细节）
         - 0-39   严重过曝或欠曝，画面信息大量丢失
      
      3. occlusion（无遮挡程度）：目标被遮挡的反面，分越高代表遮挡越少
         - 90-100 目标完全无遮挡
         - 70-89  轻微遮挡，主体仍完整可见
         - 40-69  中等遮挡，主体部分被挡住
         - 0-39   严重遮挡，主体大部分不可见
      
      4. completeness（目标完整性）：目标是否完整位于画面内
         - 90-100 目标完整在画面内
         - 70-89  目标基本完整，边缘有轻微裁切
         - 40-69  目标被画面边缘明显裁切
         - 0-39   目标大部分在画面外
      
      【标签规则】label 必须严格按以下规则从你打出的分数推导，不要独立判断：
      先取四个分数中的最低值 min_score 及其对应的维度：
      - 若 min_score >= 70，label = "清晰可用"
      - 若最低分维度是 clarity，label = "模糊"
      - 若最低分维度是 light，过曝填 "过曝"，欠曝填 "光线不足"
      - 若最低分维度是 occlusion，label = "目标遮挡"
      - 若最低分维度是 completeness，label = "目标不完整"
      
      【时间水印】若画面上存在可见的时间水印文字，提取为 time_text 字段（没有则填 null）。
      
      只输出JSON，不要任何解释，不要markdown代码块：
      {"clarity":0-100,"light":0-100,"occlusion":0-100,"completeness":0-100,"time_text":null,"label":"清晰可用|模糊|光线不足|过曝|目标遮挡|目标不完整","issue":"一句话问题描述"}"""

   c.【关键约束】_llm_chat 的 max_tokens 必须设为 4000 或更大，temperature 设为 0.1。
      该系统模型是推理模型，会先输出大量思考 token 再输出答案；
      max_tokens 过小会导致思考阶段耗尽额度，返回空字符串且不报错，造成数据静默丢失。
   d. 解析模型返回：先 strip()，若以 ``` 开头则去掉首尾 ``` 标记及可能存在的 "json" 字样，
      再用 json.loads 解析。解析失败则把该图标记为失败，保留原始返回文本前 200 字符备查。
4. 单张图片综合分 = clarity*0.3 + light*0.25 + occlusion*0.25 + completeness*0.2，保留 1 位小数。
5. 汇总统计：总图片数、成功数、失败数、总耗时秒数；四个维度各自的平均分/最低分/最高分；
   各质量标签的数量；综合分分档（>=80 优质、60-79 可用、<60 低质）的数量。
6. 生成报告 JSON 并写入 `data/downloads/{时间戳}/quality_report.json`，结构如下：
   {
     "dataset_name": "...", "total": N, "success": M, "failed": K,
     "elapsed_sec": 123.4, "concurrency": 8,
     "summary": {
       "avg_score": 0.0,
       "dimensions": {"clarity": {"avg":0,"min":0,"max":0}, "light": {...},
                      "occlusion": {...}, "completeness": {...}},
       "label_stats": {"清晰可用": N, "模糊": N, "光线不足": N, ...},
       "grade_stats": {"优质": N, "可用": N, "低质": N}
     },
     "images": [{"filename": "...", "score": 0.0, "clarity": 0, "light": 0,
                 "occlusion": 0, "completeness": 0, "label": "", "issue": "",
                 "time_text": null, "error": null}]
   }
7. 界面表格取 images 中按 score 升序的前 20 条作为 rows。

### 5.2 错误处理
- 【获取数据集信息】返回空或目录不存在 → status:failed，message 说明数据集无法访问
- 目录下无 jpg 文件 → status:failed
- 单张图片读取失败 / 大模型调用失败 / JSON 解析失败 → 该图片 error 字段记录原因，
  其余图片继续处理，绝不中断整体流程
- concurrency 小于 1 或类型非整数 → 使用默认值 8
- 全部图片都失败 → status:failed
- 至少有一张成功 → status:success

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-08-29 | 初始版本，四维度批量评估与 JSON 报告输出 |