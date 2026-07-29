---
id: mel-spectrogram-generator
name: 梅尔频谱图生成器
version: 0.1.0
type: function
language: python
status: active
created: 2026-07-28
---

# 梅尔频谱图生成器

## 1. 功能概述

将输入的音频文件(`voice`)转换为**梅尔频谱图**(Mel Spectrogram)，并保存为图像文件（如 PNG）。频谱图的横轴为时间，纵轴为梅尔频率，颜色反映能量强度，常用于语音或音乐信号的可视化与分析。

## 2. 输入规范

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| voice | string | 是 | - | 输入音频文件的路径（支持 WAV、MP3 等常见格式） |
| sr | int | 否 | 22050 | 目标采样率 (Hz)，音频会被重采样至该值 |
| n_fft | int | 否 | 2048 | FFT 窗口大小（采样点数） |
| hop_length | int | 否 | 512 | 帧移（采样点数） |
| n_mels | int | 否 | 128 | 梅尔滤波器组数量 |
| fmin | float | 否 | 0 | 最小频率 (Hz) |
| fmax | float | 否 | sr/2 | 最大频率 (Hz)，默认为奈奎斯特频率 |
| output_path | string | 否 | 自动生成 | 输出梅尔频谱图的保存路径，如未指定则在同一目录生成与音频同名的 `.png` 文件 |
| dpi | int | 否 | 100 | 输出图像的分辨率 (DPI) |

## 3. 输出规范

### 3.1 标准输出字段
| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | `success` 或 `failed` |
| message | string | 结果说明（成功信息或错误原因） |
| output_format | string | 固定为 `image` |
| data | dict | 输出数据，详见 3.2 |

### 3.2 可视化输出格式
| output_format | data 格式 | 界面渲染方式 |
|---------------|----------|-------------|
| `image` | `{"image_path": "/path/to/spectrogram.png"}` | 直接绘制图片 |

## 4. 依赖环境

| 依赖 | 版本 | 用途 |
|------|------|------|
| librosa | >=0.9.0 | 音频加载、特征提取 |
| matplotlib | >=3.5.0 | 绘制和保存频谱图 |
| numpy | >=1.20 | 数值运算 |
| soundfile | >=0.10 | 通过 librosa 自动调用，支持多种音频格式 |

## 5. 运行机制

### 5.1 执行流程
1. 检查输入文件 `voice` 是否存在，若不存在则返回错误。
2. 校验各参数范围（如 `sr` > 0，`n_mels` > 0 等）。
3. 使用 `librosa.load()` 加载音频，重采样至 `sr`。
4. 计算梅尔频谱图：`librosa.feature.melspectrogram(y, sr, n_fft, hop_length, n_mels, fmin, fmax)`，并转换为分贝单位：`librosa.power_to_db(S)`。
5. 使用 `matplotlib` 绘制频谱图（横轴时间、纵轴频率、颜色条），关闭坐标轴或按需保留。
6. 将图像保存至 `output_path`，若未提供则自动生成路径。
7. 返回成功结果，包含图像路径；若过程中捕获异常则返回错误信息。

### 5.2 错误处理
- 文件不存在 → 返回 `status: "failed"`, `message: "文件不存在: {voice}"`
- 文件无法读取或损坏 → 返回 `status: "failed"`, `message: "无法读取音频文件: {voice}"`
- 参数校验失败 → 返回 `status: "failed"`, `message: "参数无效: {具体错误}"`
- 绘图或保存异常 → 返回 `status: "failed"`, `message: "生成频谱图失败: {异常信息}"`

## 6. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-07-28 | 初始版本，支持单音频文件梅尔频谱图生成 |