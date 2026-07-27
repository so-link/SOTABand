工具名：论文摘要工具
输入：数据集名{dataset}，摘要长度{n}
过程：
1）通过【获取数据集信息】获取数据集{dataset} 的目录{data_path}
2）对目录{data_path}中的每个pdf文件，抽取文件的内容，调用deepseek v4 pro大模型得到一段不超过{n}个字的中文摘要。所有文件的中文摘要合并成一个大的摘要, 每各文件的摘要 的标题设定为文件内容中的论文标题，保存为md文件，存放在目录{data_path}下面。

输出：处理的文件的数量， 保存的数据集名{dataset}。

注：可通过【获取DeepSeek API KEY】获取deepseek大模型的API_KEY