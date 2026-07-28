工具名称：大模型批量标框

输入：1）数据集名{dataset}  2）检测目标{req}    3）标好框的数据集名{output_dataset}  
过程：
1）通过【获取豆包API KEY】获得doubao大模型的{api_key} 
2) 调用 【获取数据集信息】获取数据集{dataset}的目录{data_path_src}
3）在项目目录下新建目录: ’./data/download/{xxxx}/‘     ,{xxxx}  为当前时间戳，该目录的路径记为{data_path_target}
4）在{data_path_target} 目录下建立子目录'{data_path_target}/labeled/'
5）对目录{data_path_src}下的每张图片文件，先保持长宽比例不变的情况下压缩到640分辨率，调用 豆包大模型{doubao-seed-2-0-lite-260428} （采用的API_KEY是{api_key}）,   进行目标检测，检测目标是{req}, 得到目标的yolo风格的bounding-box 的列表。
6）根据该bounding-box,在图片中标上加粗的红框，并保存为标上红框的图片，把图片保存到目录'{data_path_target}/labeled/' 中
7) 根据yolo 训练数据集的风格，把所有图片的bounding-box 构成标签文件，连同原始图片文件，拷贝到
目录{data_path_target}中。
8）通过【数据集注册API】API将目录{data_path_target} 注册为数据集{output_dataset}。

输出：目录'{data_path_target}/labeled/'中的第1张图片
