输入：数据集名{dataset},检测目标{req}
过程：
1) 通过【获取数据集信息】获取数据集{dataset}的信息，从中解析{data_path}字段作为文件目录
2）通过【获取豆包API KEY】获得doubao大模型的API_KEY
3）对于目录{data_path}下的每一张图片，调用doubao大模型 {doubao-seed-2-1-pro-260628},进行目标检测，检测目标是{req}, 如果图片中含有目标，则得到目标的bounding-box，构建目标检测的标签文件。
4) 按yolo训练数据集的格式整理目录{data_path}

输出：目录{data_path}的文件目录结构