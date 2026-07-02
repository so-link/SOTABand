import os
import tempfile

def execute(**kwargs):
    # Validate required parameters
    if "csv_path" not in kwargs:
        return {"status": "failed", "message": "缺少必需参数: csv_path", "data": {}}
    if "field" not in kwargs:
        return {"status": "failed", "message": "缺少必需参数: field", "data": {}}
    if "length" not in kwargs:
        return {"status": "failed", "message": "缺少必需参数: length", "data": {}}

    csv_path = kwargs["csv_path"]
    field = kwargs["field"]
    length = kwargs["length"]

    # Validate length
    try:
        length = int(length)
        if length <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return {"status": "failed", "message": "length 必须是正整数", "data": {}}

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import pandas as pd

    # Read CSV
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        return {"status": "failed", "message": f"无法读取CSV文件: {str(e)}", "data": {}}

    if field not in df.columns:
        return {"status": "failed", "message": f"CSV中不存在字段: {field}", "data": {}}

    if length > len(df):
        return {"status": "failed", "message": f"length 超过数据行数 ({len(df)})", "data": {}}

    # Select last 'length' points
    data_values = df[field].iloc[-length:]

    if 'date' in df.columns:
        x = pd.to_datetime(df['date'].iloc[-length:])
        xlabel = "Date"
    else:
        x = range(length)
        xlabel = "Index"

    # Plot
    plt.figure(figsize=(10, 5))
    plt.plot(x, data_values.values)
    plt.title(f"ETTh - {field} (last {length} points)")
    plt.xlabel(xlabel)
    plt.ylabel(field)
    plt.grid(True)

    # Save to temporary image file
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img_path = tmp.name
        tmp.close()
        plt.savefig(img_path, dpi=100, bbox_inches='tight')
        plt.close()
    except Exception as e:
        plt.close()
        return {"status": "failed", "message": f"保存图像失败: {str(e)}", "data": {}}

    return {
        "status": "success",
        "output_format": "image",
        "message": f"成功生成{field}的时序图，长度{length}",
        "data": {"image_path": img_path}
    }