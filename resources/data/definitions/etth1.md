---
id: etth1
name: ETTh1
version: 1.0.0
type: tabular
status: active
created: 2025-04-04
---

# ETTh1

## 1. 数据集概述
ETTh1 全称 Electricity Transformer Temperature - hourly，即“电力变压器温度-小时级”数据，是时间序列预测领域广泛使用的公开基准数据集，记录了电力变压器两年间的运行数据，每小时采样一次。

## 2. 目录结构
```
ETTh1.csv
```

## 3. 数据格式
| 文件 | 格式 | 大小 | 说明 |
|------|------|------|------|
| ETTh1.csv | csv | 2.5MB | ETTh时序数据 |

## 4. 数据 Schema
| # | 字段名 | 数据类型 | 约束 | 描述 |
| :-: | :--- | :--- | :--- | :--- |
| 1 | `date` | DATETIME (字符串) | PRIMARY KEY, NOT NULL, 格式 `YYYY-MM-DD HH:MM:SS` | 记录时间戳，严格递增，无重复 |
| 2 | `HUFL` | FLOAT | NOT NULL | 高使用负载 (High UseFul Load)，单位: MW |
| 3 | `HULL` | FLOAT | NOT NULL | 高无用负载 (High UseLess Load)，单位: MW |
| 4 | `MUFL` | FLOAT | NOT NULL | 中等使用负载 (Medium UseFul Load)，单位: MW |
| 5 | `MULL` | FLOAT | NOT NULL | 中等无用负载 (Medium UseLess Load)，单位: MW |
| 6 | `LUFL` | FLOAT | NOT NULL | 低使用负载 (Low UseFul Load)，单位: MW |
| 7 | `LULL` | FLOAT | NOT NULL | 低无用负载 (Low UseLess Load)，单位: MW |
| 8 | `OT` | FLOAT | NOT NULL | 油温 (Oil Temperature)，单位: ℃，**预测目标变量** |

---

## 取值范围参考

| 字段 | 最小值（约） | 最大值（约） |
| :--- | :---: | :---: |
| `HUFL` | 2.5 | 7.5 |
| `HULL` | 1.0 | 3.5 |
| `MUFL` | 0.5 | 3.0 |
| `MULL` | 0.1 | 1.0 |
| `LUFL` | 2.0 | 6.0 |
| `LULL` | 0.5 | 2.5 |
| `OT` | 20 | 55 |

---

## 数据样本

```csv
date,HUFL,HULL,MUFL,MULL,LUFL,LULL,OT
2016-07-01 00:00:00,5.827,2.009,1.599,0.462,4.203,1.340,30.531
2016-07-01 01:00:00,5.828,1.998,1.607,0.461,4.230,1.321,30.421
2016-07-01 02:00:00,5.819,1.967,1.622,0.461,4.228,1.316,30.271

## 5. 数据来源
待补充

## 6. 使用场景
时间序列预测，如电力变压器温度预测等。

## 7. 质量评估
待补充

## 8. 访问权限
public

## 9. 版本历史
| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2025-04-04 | 初始版本 |