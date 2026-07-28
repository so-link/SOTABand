工具名称：大模型标框工具
输入：图片文件路径{file_path}， 检测目标{req}
1）通过【获取豆包API KEY】获得doubao大模型的{api_key} 
2）对输入的图片文件{file_path}，先保持长宽比例不变的情况下压缩到640分辨率，调用 豆包大模型{doubao-seed-2-0-lite-260428} （采用的API_KEY是{api_key}）,   进行目标检测，检测目标是{req}, 得到目标的yolo风格的bounding-box 的列表
3）根据该bounding-box,在图片中标上加粗的红框，并保存为标上红框的图片

输出：标上红框的图片