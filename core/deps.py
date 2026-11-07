"""依赖体检：工具代码的第三方 import → pip 包名 → 当前执行环境是否就绪。

单独成模块的原因：同一张「import 名 ↔ pip 包名」映射表被四处需要——
  * 依赖检测（/api/tool/{id}/check-deps、导入后体检）
  * 依赖安装（install_deps_stream 判断"是否已装"必须用 import 名）
放在 core 层避免各写一份，出现 `cv2` 与 `opencv-python` 互相误判。

映射方向说明：
  * 检测：`import PIL` → 缺 → 安装时得用 `pillow`
  * 安装前判断：依赖写的是 `opencv-python`，不能 `import opencv-python`
    （语法错误），必须反查成 `cv2`
"""

from __future__ import annotations

import re
import shlex
import subprocess

# import 名 → pip 包名（小写匹配）
# 只收「import 名与 pip 包名不同」的常见项；同名的不必登记（pip_name_for 会原样返回）。
# 漏登记的后果很具体：`pip install Crypto` / `pip install fitz` 直接失败 →
# 沙箱里反复报"依赖不可用"，靠 LLM 换库兜圈。
PIP_NAME_BY_IMPORT: dict[str, str] = {
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "serial": "pyserial",
    "OpenSSL": "pyopenssl",
    "jwt": "pyjwt",
    "win32com": "pywin32",
    "win32api": "pywin32",
    "win32con": "pywin32",
    "win32gui": "pywin32",
    "pkg_resources": "setuptools",
    "fit_zoo": "scipy",
    # 常见「包名 ≠ import 名」
    "Crypto": "pycryptodome",
    "fitz": "pymupdf",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "jose": "python-jose",
    "magic": "python-magic",
    "attr": "attrs",
    "Bio": "biopython",
    "fpdf": "fpdf2",
    "Levenshtein": "python-Levenshtein",
    "MySQLdb": "mysqlclient",
    "psycopg2": "psycopg2-binary",
    "umap": "umap-learn",
    "gi": "PyGObject",
    "wx": "wxPython",
    "barcode": "python-barcode",
    "slugify": "python-slugify",
}

# pip 包名（小写）→ import 名
IMPORT_NAME_BY_PIP: dict[str, str] = {
    pip.lower(): imp for imp, pip in PIP_NAME_BY_IMPORT.items()
}

# 依赖字符串里的版本/额外标记分隔符：numpy>=1.26 / pkg[extra] / pkg==1.0
_SPEC_SPLIT = re.compile(r"[<>=!~;\[\s,]")


def pip_name_for(module: str) -> str:
    """import 名 → pip 包名（无映射时原样返回）"""
    return PIP_NAME_BY_IMPORT.get(module, module)


def import_name_for(dep_spec: str) -> str:
    """pip 依赖串 → import 名（`opencv-python>=4.8` → `cv2`）"""
    base = _SPEC_SPLIT.split((dep_spec or "").strip())[0].strip()
    if not base:
        return ""
    return IMPORT_NAME_BY_PIP.get(base.lower(), base)


# 运行时异常文本里的模块名：No module named 'numpy'
_NO_MODULE_RE = re.compile(r"No module named '([^']+)'")


def extract_missing_module(payload: dict | None, extra_text: str = "") -> str:
    """从一次执行结果里识别「缺失的第三方模块名」，识别不到返回空串。

    必须覆盖两条来源，否则自动调试会陷入死循环：

    1. 平台执行包装器捕获到的 ``ModuleNotFoundError``（模块级 import）——
       直接给出 ``error`` / ``missing_module`` 字段；
    2. **工具自身 try/except 吞掉**的情况：生成的工具代码强制带顶层
       ``try/except Exception``，只要 import 写在 ``execute()`` 里（torch、cv2
       这类重库很常见），异常就不会冒泡到包装器，只会变成
       ``message = "工具执行失败: No module named 'xxx'"``。
       历史实现只认第 1 种，于是系统完全判不出"缺依赖"，只能把报错丢给
       LLM 让它换库，一轮换一个库、直到调试轮次耗尽。

    Args:
        payload: 解析后的执行结果 dict（可能为 None）
        extra_text: 额外文本（通常是子进程 stderr）
    """
    data = payload if isinstance(payload, dict) else {}
    if data.get("error") == "ModuleNotFoundError" and data.get("missing_module"):
        return str(data["missing_module"]).split(".")[0].strip()

    for key in ("missing_module", "message", "stderr", "traceback"):
        text = str(data.get(key) or "")
        m = _NO_MODULE_RE.search(text)
        if m:
            return m.group(1).split(".")[0].strip()

    m = _NO_MODULE_RE.search(extra_text or "")
    if m:
        return m.group(1).split(".")[0].strip()
    return ""


def _split_command(command: str) -> list[str]:
    """把一条命令行拆成 argv（Windows 路径的反斜杠不能被当转义符）。"""
    text = (command or "").strip()
    if not text:
        return []
    try:
        parts = shlex.split(text, posix=False)
    except Exception:
        parts = text.split()
    # 去掉包裹的引号：shlex(posix=False) 会保留它们
    return [p.strip().strip('"').strip("'") for p in parts if p.strip()]


def build_pip_argv(pip_base: list[str], dep: str, adjusted: str | None = None) -> list[str]:
    """把「依赖名」或 LLM 返回的整条安装命令，统一成 pip 的 argv。

    历史 bug：``adjusted_command`` 的语义是**完整命令**
    （``pip install xxx --no-deps``），却被当成依赖名拼进
    ``[python, -m, pip, install, dep]``，实际执行的是
    ``pip install "pip install xxx --no-deps"`` —— 重试通道形同虚设，
    永远装不上，表现为「反复报缺失依赖」。

    这里只保留命令里 ``install`` 之后的参数（包名 + 选项），前缀统一换成
    调用方给定的 ``pip_base``（保证装进工具实际使用的那个解释器）。
    """
    base = list(pip_base)
    # --prefer-binary：优先装预编译 wheel。
    # 不加的话 pip 会为了「更新的版本」去源码编译 Pillow / greenlet 等 C 扩展，
    # 而精简系统/容器常缺 gcc 与 libjpeg/zlib 头文件，报错形如
    #   Failed to build installable wheels for ... ╰─> Pillow
    # 明明镜像上有可用 wheel，却卡在编译上。
    if not adjusted:
        return base + ["install", "--prefer-binary", dep]

    parts = _split_command(adjusted)
    if not parts:
        return base + ["install", "--prefer-binary", dep]
    if "install" in parts:
        tail = parts[parts.index("install") + 1:]
    else:
        # 只给了选项/包名（没有 install 关键字）→ 补上子命令
        tail = parts
    # LLM 给的调整命令也可能漏掉 --prefer-binary；
    # 除非它显式要求从源码构建（--no-binary / --only-binary），否则补上。
    if not any(p.startswith(("--no-binary", "--only-binary")) for p in tail) \
            and "--prefer-binary" not in tail:
        tail = ["--prefer-binary"] + tail
    return base + ["install"] + tail


def dist_name_for(dep_spec: str) -> str:
    """依赖串 → **发行版名**（去掉版本号 / extras 之后再映射）。

    用于 ``importlib.metadata.version(...)`` 这类按发行版判断"装了没"的场景：
    ``numpy>=1.26`` → ``numpy``；``opencv-python>=4.8`` → ``opencv-python``；
    ``cv2`` → ``opencv-python``。

    为什么不能直接 `import` 判断：PyPI 上存在与 import 名同名的**无关包**
    （例如 `barcode` 是个 DNA 序列工具，`python-barcode` 才是条码库）。
    `import barcode` 成功并不代表你要的那个库在，必须核对发行版。
    """
    base = _SPEC_SPLIT.split((dep_spec or "").strip())[0].strip()
    if not base:
        return ""
    return pip_name_for(base)


# 「依赖环境」表里属于标准库/内建的写法
_BUILTIN_MARKERS = ("内建", "内置", "标准库", "stdlib", "builtin", "built-in")
_SKIP_DEP_NAMES = {"无", "none", "python", "python3", "系统自带"}
_DEP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")


def extract_declared_deps(spec_md: str) -> list[str]:
    """从 MD 的「## N. 依赖环境」表格里提取**pip 包名**（保序去重）。

    为什么必须以 MD 为准：``import 名 → pip 包名`` 不存在唯一反推。
    典型例子：``barcode`` 这个 import 名，PyPI 上有两个发行版都能提供它 ——
    ``barcode``（DNA 序列工具）和 ``python-barcode``（条码库）。只靠映射表猜
    就会装错包（PIP_NAME_BY_IMPORT 里写死的是常见那个）。

    而 MD 的依赖表写的是**使用者/模型明确指定的安装名**，是唯一能消歧的信息。
    所以自动调试的安装顺序是：先按 MD 声明装，再用 AST import 兜底补漏。

    表格形态（项目 57 个工具统一如此）::

        ## 4. 依赖环境

        | 依赖 | 版本 | 用途 |
        |------|------|------|
        | python-barcode | >=0.15 | 生成条码（import barcode） |
        | pillow | - | 图片处理 |
    """
    deps: list[str] = []
    if not spec_md:
        return deps

    inside = False
    for raw in spec_md.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            inside = line.startswith("##") and "依赖" in line
            continue
        if not inside or not line.startswith("|"):
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        name = cells[0].strip().strip("`").strip()
        if not name or set(name) <= {"-", ":", " "}:
            continue
        if name == "依赖":          # 表头
            continue
        low = name.lower()
        if low in _SKIP_DEP_NAMES or low.startswith("python"):
            continue
        if any(m in low for m in _BUILTIN_MARKERS):
            continue
        kind = cells[1].strip("`").strip().lower() if len(cells) > 1 else ""
        if any(m in kind for m in _BUILTIN_MARKERS):
            continue
        # 允许写成 `numpy>=1.26`：校验时只看包名部分，安装时保留原始版本约束
        if not _DEP_NAME_RE.match(_SPEC_SPLIT.split(name)[0].strip()):
            continue

        if name not in deps:
            deps.append(name)
    return deps


def dist_name_from_spec(dep_spec: str) -> str:
    """依赖串 → 发行版名，**不做 import→pip 映射**。

    调用方已经给出 pip 包名时用它（例如 MD 依赖表里写 `barcode` 就有 DNA 那个
    包的本意，不能再被映射成 `python-barcode`）。
    """
    return _SPEC_SPLIT.split((dep_spec or "").strip())[0].strip()


def extract_imports(code: str) -> list[str]:
    """从工具代码里静态提取第三方 import（复用构建器的 AST 实现）"""
    if not (code or "").strip():
        return []
    # 去掉 BOM：Windows 编辑器保存的文件常带 \ufeff，ast.parse 会直接报错，
    # 依赖被静默漏检（表现为「导入成功但依赖永远显示齐备」）。
    code = code.lstrip("\ufeff")
    try:
        from core.resource.builder.tool_builder import ToolCodeBuilder
        return ToolCodeBuilder._extract_imports(code)
    except Exception:
        return []


def probe_import(python_exe: str, module: str, timeout: int = 15) -> bool:
    """用指定解释器尝试 import，判断是否就绪。"""
    if not module:
        return True
    try:
        proc = subprocess.run(
            [python_exe, "-c", f"import {module}"],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode == 0
    except Exception:
        # 探测本身失败（超时/解释器不可用）不计为缺失，避免误报
        return True


def check_dependencies(
    code: str,
    python_exe: str,
    env_label: str = "",
    tool_id: str = "",
) -> dict:
    """体检一段工具代码的依赖。返回结构前后端共用：

    {"tool_id", "deps", "missing", "install", "pairs", "python", "env"}
    * deps     : 提取到的 import 名
    * missing  : 缺失的 import 名
    * install  : 与 missing 一一对应的 pip 包名
    * pairs    : [{"import": "cv2", "pip": "opencv-python"}]
    """
    deps = extract_imports(code)
    missing: list[str] = []
    pairs: list[dict] = []
    for mod in deps:
        if probe_import(python_exe, mod):
            continue
        missing.append(mod)
        pairs.append({"import": mod, "pip": pip_name_for(mod)})
    return {
        "tool_id": tool_id,
        "deps": deps,
        "missing": missing,
        "install": [p["pip"] for p in pairs],
        "pairs": pairs,
        "python": python_exe,
        "env": env_label,
    }


def check_tool_dependencies(tool_id: str, code: str | None = None) -> dict:
    """体检一个已注册工具（自动定位它的实现代码与执行解释器）。"""
    from core.executor.tool_executor import ToolExecutor
    from core.resource.registry.tool_registry import ToolRegistry

    registry = ToolRegistry()
    if code is None:
        code = registry.read_code_text(tool_id)

    python_exe = ToolExecutor._get_python_exe(tool_id)
    lowered = python_exe.replace("\\", "/").lower()
    if "/implementations/" in lowered and "/.venv/" in lowered:
        env_label = "tool_venv"
    elif "/venv/" in lowered:
        env_label = "project_venv"
    else:
        env_label = "system"

    return check_dependencies(code, python_exe, env_label, tool_id)


__all__ = [
    "IMPORT_NAME_BY_PIP",
    "PIP_NAME_BY_IMPORT",
    "build_pip_argv",
    "check_dependencies",
    "check_tool_dependencies",
    "dist_name_for",
    "dist_name_from_spec",
    "extract_declared_deps",
    "extract_imports",
    "extract_missing_module",
    "import_name_for",
    "pip_name_for",
    "probe_import",
]
