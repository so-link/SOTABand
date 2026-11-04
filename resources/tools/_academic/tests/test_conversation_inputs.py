"""Natural replies through real tool state, without JSON input or mocked execution."""

import secrets
from pathlib import Path

import pytest

from resources.tools._academic.common import ToolError, invoke
from resources.tools._academic.papers import parse_tracking_request
from resources.tools._academic.reproduction import parse_reproduction_request
from resources.tools._academic.writing import parse_writing_request


def pending(tmp_path, label):
    project = invoke("writing", {"mode": "create", "title": label})["data"]
    config = tmp_path / (secrets.token_hex(8) + " 配置.json")
    failed = invoke("writing", {"mode": "export", "project_id": project["id"], "format": "markdown", "config_file": str(config)})
    assert failed["status"] == "failed"
    return project, config, failed["data"]["recovery"]["id"]


@pytest.mark.parametrize("reply", [
    "配置好了，继续", "我已经安装好了，再试一次", "请继续刚才的任务", "麻烦你重试一下",
    "修复完成，请继续", "帮我再试一次吧", "我已处理完毕，重试刚才的操作", "继续。",
])
def test_natural_retry_does_real_export(tmp_path, reply):
    project, config, _ = pending(tmp_path, "自然语言导出验收 " + secrets.token_hex(4))
    config.write_text("{}", encoding="utf-8")
    response = invoke("writing", {"request": reply})
    assert response["status"] == "success", response
    assert Path(response["data"]["file_path"]).read_text(encoding="utf-8").startswith("# " + project["title"])


def test_choice_numbers_stay_bound_to_displayed_tasks(tmp_path, store):
    for index in range(3):
        pending(tmp_path, f"待处理文稿 {index}")
    menu = invoke("writing", {"request": "继续"})
    assert menu["data"]["needs_input"]
    chosen = menu["data"]["choices"][1]
    selected = store.get("recovery", chosen["id"])
    Path(selected["params"]["config_file"]).write_text("{}", encoding="utf-8")
    pending(tmp_path, "列表展示后新增的文稿")
    response = invoke("writing", {"request": "第二个"})
    assert response["status"] == "success", response
    assert response["data"]["recovery"]["id"] == chosen["id"]
    assert all(entry["attempts"] == 0 for entry in store.find("recovery") if entry["id"] != chosen["id"])


def test_config_path_can_be_supplied_in_plain_language(tmp_path):
    _, _, ident = pending(tmp_path, "替换配置文件路径")
    corrected = tmp_path / "已经配置好.json"
    corrected.write_text("{}", encoding="utf-8")
    response = invoke("writing", {"request": f'配置文件在“{corrected}”，请继续'})
    assert response["status"] == "success", response
    assert response["data"]["recovery"]["id"] == ident


def test_incomplete_reproduction_collects_source_then_command(tmp_path, store):
    initial = invoke("reproduction", {"request": "帮我复现代码，使用一张GPU，超时两分钟"})
    assert initial["data"]["error_code"] == "INPUT_REQUIRED", initial
    source = tmp_path / "尚未下载的真实源码目录"
    second = invoke("reproduction", {"request": f'源码在“{source}”'})
    assert second["data"]["details"]["field"] == "command", second
    third = invoke("reproduction", {"request": "python train.py"})
    assert third["status"] == "failed"  # The directory is actually absent; never invent code.
    assert third["data"]["error_code"] != "INPUT_REQUIRED", third
    assert "source_path" in third["message"]
    assert not store.find("job")


@pytest.mark.parametrize("word,count", [("一", 1), ("两", 2), ("三", 3)])
@pytest.mark.parametrize("phrase", ["运行命令为 python train.py", "执行：python train.py", "启动 python train.py"])
def test_human_resource_units_are_not_silently_defaulted(tmp_path, word, count, phrase):
    parsed = parse_reproduction_request(f'复现“{tmp_path}”，用{word}块显卡，{phrase}，最多跑两分钟')
    assert parsed["plan"]["gpu_count"] == count
    assert parsed["plan"]["timeout_seconds"] == 120
    assert parsed["plan"]["command"] == ["python", "train.py"]


@pytest.mark.parametrize("year", [2019, 2020, 2021, 2022])
def test_single_year_is_respected(year):
    parsed = parse_tracking_request(f"查找 JMLR {year} 年的图神经网络论文")
    assert parsed["since"] == f"{year}-01-01"
    assert parsed["until"] == f"{year}-12-31"


@pytest.mark.parametrize("phrase,suffix", [
    ("导出为 Markdown", ".md"), ("不要 PDF，导出 Word", ".docx"),
    ("导出为 BibTeX", ".bib"), ("不要导出 PDF，导出 Markdown", ".md"),
])
def test_natural_export_selects_requested_format(phrase, suffix):
    invoke("writing", {"mode": "create", "title": "PDF 数据处理说明"})
    response = invoke("writing", {"request": phrase})
    assert response["status"] == "success", response
    assert Path(response["data"]["file_path"]).suffix == suffix


def test_export_asks_format_then_accepts_short_reply():
    invoke("writing", {"mode": "create", "title": "工具操作过程记录"})
    prompt = invoke("writing", {"request": "导出文稿"})
    assert prompt["data"]["needs_input"]
    response = invoke("writing", {"request": "Word 就行"})
    assert response["status"] == "success", response
    assert Path(response["data"]["file_path"]).suffix == ".docx"


def test_export_multiple_projects_accepts_ordinal(store):
    for title in ("第一篇操作记录", "第二篇操作记录"):
        invoke("writing", {"mode": "create", "title": title})
    menu = invoke("writing", {"request": "导出 Markdown"})
    assert menu["data"]["needs_input"]
    selected_id = store.get("recovery_menu", "writing")["ids"][1]
    response = invoke("writing", {"request": "第二个"})
    assert response["status"] == "success", response
    assert store.get("writing", selected_id)["title"] in Path(response["data"]["file_path"]).read_text(encoding="utf-8")


def test_format_prompt_rejects_task_number_and_unknown_format(store):
    project = invoke("writing", {"mode": "create", "title": "导出格式交互验收"})["data"]
    invoke("writing", {"request": "导出文稿"})
    number = invoke("writing", {"request": "第二个"})
    assert number["data"]["error_code"] == "INPUT_REQUIRED"
    assert "格式" in number["message"]
    unknown = invoke("writing", {"request": "CSV 就行"})
    assert unknown["data"]["needs_input"]
    assert len(store.find("writing")) == 1
    assert store.get("writing", project["id"])["sections"] == {}
    cancelled = invoke("writing", {"request": "取消导出"})
    assert cancelled["status"] == "success"
    assert not store.find("recovery_menu")


@pytest.mark.parametrize("entry", ["vit_jax/main.py", "src/train.py", "/workspace/main.py"])
def test_command_path_is_not_a_source_directory(entry):
    parsed = parse_reproduction_request(f"用一张GPU，运行 python {entry}，超时90秒", require_source=False)
    assert parsed["source_path"] is None
    assert parsed["plan"]["command"] == ["python", entry]


@pytest.mark.parametrize("constraint", ["最多跑半小时", "用1.5张GPU", "内存4000MB", "仅用CPU"])
def test_unsupported_constraints_are_not_changed_to_defaults(constraint):
    with pytest.raises(ToolError):
        parse_reproduction_request(f"运行 python src/train.py，{constraint}", require_source=False)


@pytest.mark.parametrize("description", [
    "生成论文提纲，只依据所给摘要，不编造实验结果",
    "根据摘要生成中文大纲",
    "参考材料里的方法描述生成论文提纲",
    "根据相关工作，为全文生成提纲",
])
def test_evidence_description_does_not_select_outline_section(description):
    parsed = parse_writing_request(description)
    assert parsed["action"] == "outline"
    assert parsed["section"] == "outline"


def test_explicit_section_outline_is_preserved():
    assert parse_writing_request("为引言写提纲")["section"] == "引言"
