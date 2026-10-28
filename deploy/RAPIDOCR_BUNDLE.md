# 新 OCR 整合包配套

适用于 Windows 64 位、Python 3.10 的 OAS 整合包。此文件所在版本已接入自动安装，旧版代码须先更新。配套包不是独立 OASX 客户端。

## 新用户

制作 OAS 整合包时，把离线配套 ZIP 的 `deploy` 目录合并到脚本根目录，和 `toolkit` 同级。保留 `deploy/rapidocr-wheels` 中的 27 个 wheel，不要打包已经创建的 venv。

首次运行安装器、OASX 后端 `server.py` 或旧版 GUI 时，自动检查并建立本机 OCR 环境。离线资源包含两个 PP-OCRv6 small 模型，不需要在首次识别时另行下载。离线包只覆盖 OCR 依赖，原 OAS 基础依赖和 Python 仍由原整合包提供。

## 老用户

更新后的 OASX 后端在拉取代码后准备 OCR。第一次从旧更新器升级时，旧进程还未加载新逻辑，因此重启 OASX 后端后自动准备。直接拉取代码的用户也由下次后端启动或第一次创建 OCR 实例补齐。

有 `deploy/rapidocr-wheels` 就校验并离线安装；没有则根据固定版本及 SHA256 清单下载。尊重部署配置的 `PypiMirror`，不关闭 TLS 校验。也可双击 `deploy/install-rapidocr.cmd` 手动准备。

## 保护与回退

- 原 `toolkit` 的包不升级、不卸载。新环境继承基础依赖，新 OCR 的包只安装在 `runtime/rapidocr` 的独立版本目录中。
- 多进程共用安装锁。依赖、模型校验和真实数字识别自检成功后，才原子写入 `active.json`。失败保留原活动记录，自动模式会明确提示并使用旧 OCR。
- 重复启动只校验并复用，不重复安装。搬动安装目录或升级基础 Python 后会重新建立环境，不能复制别人的 venv。
- 不修改账号 JSON；已有 `config/ocr_runtime/accounts.json` 的人工选择优先保留，自动安装不会覆盖它。
- `AutoInstallRapidOcr: false` 关闭自动准备及自动选择，恢复部署配置的后端；设置进程环境变量 `OAS_OCR_BACKEND=legacy` 可明确强制旧 OCR。
- 新后端仅对新建 OCR 实例生效，不重启已有脚本、模拟器、ADB 或服务。
- 当前离线包仅支持 Windows x64 / CPython 3.10，其他平台自动模式保留旧 OCR，手动安装明确报错。

## 可复现打包

先使用 Python 3.10 x64 下载固定 wheel：

```powershell
.\toolkit\python.exe -m pip download --only-binary=:all: --require-hashes -r deploy/requirements-rapidocr-lock.txt --dest wheelhouse
.\toolkit\python.exe dev_tools/build_rapidocr_bundle.py --wheels wheelhouse --output OAS-RapidOCR-win64-py310.zip
```

ZIP 内只有公开安装工具、依赖和模型，不包含账号、路径选择文件、日志、凭据或已安装环境。生成的 `.sha256` 文件用于分发校验。wheel 内保留各依赖的许可证信息。
