输入：用户需求{req}，数量{n}，发表年限{year}，数据集名称{dataset}
过程：
1）调用【【Lens专利检索与注册工具】】，所采用的参数：用户需求{req}，数量{n}，发表年限{year}，数据集名称{dataset}
2）调用【获取DeepSeek API KEY】获取api_key
3)   调用【获取数据集信息】获取数据集{dataset}的目录的路径{data_path}
4）调用deepseek v4 pro 大模型，对目录{data_path}下的所有md文件的内容全文翻译为中文，替换原来的md文件的内容
输出：{data_path}