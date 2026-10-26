# 可选的 rapid-small OCR

默认后端仍为 `legacy`。`rapid-small` 使用 CPU 上的 PP-OCRv6 small，默认推理线程数为 1。
依赖或本地模型缺失时直接报错，不会静默换模型或自动下载。适配器保留原有
`TextSystem` 的检测、识别和裁剪接口；应用入口使用 RGB，协同对弈的 `TimedOCR`
显式使用 BGR。当前不支持方向分类、GPU 和字符白名单。

## 独立运行环境

在新的 Windows Python 3.10 虚拟环境中安装项目的基础运行依赖，再安装
`requirements-rapidocr.txt` 覆盖依赖。已有完整基础环境时，可建立使用其
site-packages 的独立虚拟环境。不要直接升级正在运行的 toolkit。

依赖文件固定 RapidOCR、ONNX Runtime、NumPy、OpenCV 等版本。适配器要求
RapidOCR 包内的 `models/PP-OCRv6_det_small.onnx` 和 `PP-OCRv6_rec_small.onnx`
存在；仅安装依赖不等于识别准确率已通过验证。

## 按账号选择（仅影响之后启动的进程）

在本机创建 `config/ocr_runtime/accounts.json`，例如：

```json
{
  "runtime_python": "C:/path/to/project/.venv-rapid/Scripts/python.exe",
  "accounts": {
    "example-account": {"backend": "rapid-small", "enabled_after": 0}
  }
}
```

配置名必须精确匹配账号配置文件名（不含 `.json`）。`enabled_after` 是 Unix 秒数：
早于该时刻创建的进程不选用此项；选择结果在进程内缓存，不会热切换正在运行的任务。
可增加 `default` 项，结构与单账号项相同，但仅应用于确实存在账号配置文件的进程。
账号配置和运行时路径属于本机数据，已加入 Git 忽略规则。

推理在独立 Python 子进程中通过标准输入/输出通信，不监听网络端口；主进程退出后
子进程会清理。回退时删除对应选择项或设为 `legacy`，并在下次启动任务时生效。
`legacy` 账号项表示不采用独立工作进程；最终后端仍由环境变量或部署配置决定。

优先级为 `OAS_OCR_BACKEND` 环境变量、账号独立运行时、部署配置的 `OcrBackend`。
环境变量和部署配置直接选择当前 Python 环境中的后端，不使用账号指定的独立 Python。
使用远程 OCR 服务（`UseOcrServer=true`）时，后端应在服务端选择。

## 离线验证

在具有项目基础依赖和 pytest 的开发环境中执行：

```powershell
python -m pytest -q tests/test_account_ocr_runtime.py tests/test_rapidocr_adapter.py
```

这两组测试使用临时文件及模拟推理，验证选择优先级、颜色、裁剪和接口兼容性，
不会启动游戏。它们不证明真实图片准确率、运行时模型可用或完整任务完成。

`dev_tools/run_ocr_verification.py` 可在 Windows 上运行自备语料的实际推理验证，
参数为 `--python`、`--corpus`、`--output`，可加 `--isolated-python`。
语料目录需提供 `manifest.json`，包含图片路径、SHA-256、标注和原始帧路径；
字段以 `verify_rapidocr.py` 的读取逻辑为准。每次使用新的输出目录，语料和报告留在本机。
`probe_account_ocr.py` 是针对标注文字为“萤草”的单图启动/退出探针，并非通用准确率测试。
