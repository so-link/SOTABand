import json
def execute(**kwargs):
    json_str = kwargs.get("json_str", "")
    try:
        data = json.loads(json_str)
        return {"status": "success", "message": "Formatted", "data": json.dumps(data, indent=2)}
    except Exception as e:
        return {"status": "failed", "message": str(e), "data": None}
