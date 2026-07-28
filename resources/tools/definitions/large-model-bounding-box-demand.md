工具名称：大模型标框
输入：图片文件{img}， 检测目标{req}
1）通过【获取豆包API KEY】获得doubao大模型的信息，只从中读取API_KEY {api_key}，不读取其他信息
2）对输入的图片文件，调用doubao大模型（采用的API_KEY是{api_key}，采用的模型是 {doubao-seed-2-1-pro-260628}）,   进行目标检测，检测目标是{req}, 得到目标的yolo风格的bounding-box 的列表
3）根据该bounding-box,在图片中标上加粗的红框，并保存为标上红框的图片

输出：标上红框的图片