## luchi 分支

本分支基于 [runhey/OnmyojiAutoScript](https://github.com/runhey/OnmyojiAutoScript)，于 2026-10-08 整理公开。保留上游 GPLv3 许可证。

2026-10-11：同步当前定制版的地域鬼王选择与每日计数、庭院事务“龙仪星引”识别和固定排期、预设切换后的准备流程、新旧确认弹窗与新版奖励素材、重启开关约束、手动关闭模拟器后的唤醒检查、结界挂卡默认第一张，以及百鬼棋局每周 600 鼬乐币通知。新增 [彼世逢魔](tasks/OtherWorldTwilight/README.md)，默认关闭。对应功能已做离线回归；这不等于每项功能都已完成游戏内实战验证。

增强内容包括多庭院皮肤和庭院事务、寮任务与开场重试、每日收尾与反馈、账号会话保护、配置导入导出与日志统计，以及战斗、奖励领取和导航修复。百鬼棋局（Chess）模块移植自 [xylolit-mu/OnmyojiAutoScript](https://github.com/xylolit-mu/OnmyojiAutoScript)，来源与适配说明见 [Chess 文档](tasks/Chess/README.md)。感谢两项目的原作者与贡献者。

配套前端为 [OASX](https://github.com/luchi5/OASX)，管理面板为 [OAS-WebPanel](https://github.com/luchi5/OAS-WebPanel)。部署时自行配置设备、账号和通知服务。仓库只提供通用模板与识别素材。

### 同步主仓库

本仓库直接 fork 自 `runhey/OnmyojiAutoScript`，默认 `luchi` 分支保留定制功能，并以 runhey 为代码同步源。

```shell
git remote add upstream https://github.com/runhey/OnmyojiAutoScript.git
git fetch upstream
git switch luchi
git merge upstream/master
```

已配置 `upstream` 时跳过第一行，先检查本地修改和合并结果再更新运行环境。GitHub 网页的 fork 同步来源也直接指向 runhey；定制分支同步后仍需检查合并结果并运行相关测试。

每日反馈由管理面板展示。反馈链接默认使用 `http://127.0.0.1:22300`；如需通过公网通知打开，在启动后端与管理面板前将 `OAS_PUBLIC_FEEDBACK_ORIGIN` 设置为面板的实际 HTTP(S) origin，例如 `https://feedback.example.invalid`。本地 `config/daily_feedback/settings.json` 中的 `public_origin` 优先于环境变量；无效配置会回退到环境变量或本地默认值。origin 只包含协议、主机和可选端口。反馈功能的启用账号由本地 `config/daily_feedback/settings.json` 配置，该文件及生成的截图、报告均不提交到 Git。通知服务凭据通过各账号的本地配置填写。

纯功能单测位于 `dev_tools/test_*.py` 与 `tests/test_*.py`，使用临时目录或模拟设备验证逻辑。不要将账号配置、运行日志、截图、证书或设备路径作为提交内容。

拉取本分支：

```sh
git clone --branch luchi https://github.com/luchi5/OnmyojiAutoScript.git
```

已有安装需在本地 `config/deploy.yaml` 中将 `Repository` 设为 `https://github.com/luchi5/OnmyojiAutoScript.git`，`Branch` 设为 `luchi`，更新器才会继续使用本定制分支。显式配置的仓库与分支会被保留。后续跟进上游使用 `upstream` 指向 runhey 仓库，在同步前人工检查并合并改动。

### 初次安装与启动（Windows）

本仓库提供主脚本源码。GitHub 的 `Code → Download ZIP` 和自动生成的 `Source code.zip` 不包含 `toolkit` 运行环境或 `oas.exe` 启动器，不能当作完整安装包使用。配套 OASX 的编译包只提供图形客户端，网页面板也需要已经安装的 OAS 后台。

初次使用直接下载本仓库 Releases 中的预配置安装包：

1. 从 [本仓库 Easy install](https://github.com/luchi5/OnmyojiAutoScript/releases/tag/easy-install-luchi-v1.0.0) 下载 `OnmyojiAutoScript-easy-install-luchi.rar`，完整解压到新的目录。安装包来自 [runhey 的原版 Easy install v1.0.0](https://github.com/runhey/OnmyojiAutoScript/releases/tag/oas-install-v1.0.0)，只修改了包内的 `config/deploy.yaml`，保留原版 `oas.exe`、`toolkit` 和安装流程。
2. 包内已经设置本仓库地址、`luchi` 分支、`ReplaceAdb: false`、官方 PyPI 依赖源，以及配套 OASX 使用的 `22289` 端口；无需先下载主仓库安装包，也无需手动修改 YAML。
3. 双击安装包的 `oas.exe` 完成代码拉取和依赖安装。旧安装器首次可能仍加载旧部署模块；更新后退出程序，再执行一次安装器，按下文核对官方 ADB 的安装日志。只在没有运行任务、模拟器和 ADB 服务时执行完整安装器。
4. 后台就绪后，再打开 [OASX 编译版](https://github.com/luchi5/OASX/releases) 或 [网页面板](https://github.com/luchi5/OAS-WebPanel)。客户端的连接地址要使用后台日志中显示的实际端口。

`config/deploy.yaml` 是每台电脑的本地部署配置，不提交真实配置。通用模板已经在 [deploy/template](deploy/template)，首次加载部署配置时会据此生成本地 YAML。需要提前编辑时，可以在主脚本根目录执行以下 PowerShell 命令；它不会覆盖现有配置：

```powershell
if (!(Test-Path -LiteralPath config/deploy.yaml)) {
    New-Item -ItemType Directory -Path config -Force | Out-Null
    Copy-Item deploy/template config/deploy.yaml
}
```

`oas.exe` 的作用是启动安装和后台流程，主脚本本身由 Python 执行。已经准备好运行环境和依赖时，无需这个 EXE；在主脚本根目录执行：

```powershell
.\toolkit\python.exe -X utf8 -B server.py --host 127.0.0.1 --port 22289
```

这条命令启动后台，不运行安装器。通用模板中的 `Deploy.Webui.Run: null` 默认不自动启动账号任务；如果已有配置指定了 `Run`，后台仍会按该配置启动对应账号。OASX 连接 `127.0.0.1:22289`；网页面板的 `backend_url` 填 `http://127.0.0.1:22289`，`backend_root` 填该主脚本目录。关闭这条命令所在的终端会结束后台，因此运行任务期间保持终端打开。

### Windows ADB 自动安装

Windows 安装器在 Python 依赖安装完成后，自动下载 Google 官方 **Platform-Tools 37.0.1**，核对固定版本压缩包及四个 EXE/DLL 的 SHA-256，再安装到默认的 `toolkit/Lib/site-packages/adbutils/binaries`。`adbutils==0.11.0` 保留为 Python 接口，不再使用它附带的旧版 ADB。已安装且四个文件完全匹配时跳过下载；重新安装依赖带回旧文件后，下次执行安装器会再次校验升级。Linux/macOS 和用户自行指定的 ADB 路径不受这一步管理。

已有安装使用默认 ADB 路径时，还需将 `config/deploy.yaml` 的 `Deploy.Adb.ReplaceAdb` 设为 `false`。新版安装模板默认关闭它：旧的模拟器替换逻辑只复制 EXE，无法完整提供 37.0.1 所需的 DLL，安装器会明确阻止这种组合。此功能管理脚本自带的 ADB，不覆盖模拟器自己的程序文件。

通过原安装器执行更新才能安装 ADB；仅在网页拉取 Git 代码不会运行二进制安装。首次从旧仓库切换时，旧安装器可能已经在内存中载入旧部署模块：先拉取代码，退出刚启动的程序后再执行一次安装器，确认日志出现 `Official ADB 37.0.1: installed` 或 `already_current`。升级前应退出任务、模拟器和已有 ADB 服务；新步骤不会主动停止或启动它们，发现 ADB 运行中或文件被占用就停止升级。原安装器自身仍会执行其既有的进程处理和设备连接步骤，因此不要在账号运行时执行整套安装器。

校验或下载失败时不启动旧版 ADB。成功替换前保存精确前像，失败时尝试恢复已替换且未被再次修改、未被加载的文件。压缩包、原版 NOTICE、备份及安装结果位于默认 ADB 目录的 `.official-adb` 子目录；锁冲突不按锁龄抢占。版本固定为 37.0.1，不会随官方 `latest` 链接自动换成未经验证的新版本。

离线验证：`python -B tests/test_official_adb.py -v`，测试只使用临时目录和模拟数据，不连接设备、不执行 ADB、不修改真实配置。

---

<div align="center">

# OnmyojiAutoScript

<br>

<div>
    <img alt="python" src="https://img.shields.io/badge/python-3.10-%233776AB?logo=python">
</div>
<div>
    <img alt="platform" src="https://img.shields.io/badge/platform-Windows-blueviolet">
</div>
<div>
    <img alt="license" src="https://img.shields.io/github/license/runhey/OnmyojiAutoScript">
    <img alt="GitHub repo size" src="https://img.shields.io/github/repo-size/runhey/OnmyojiAutoScript">
    <img alt="GitHub all releases" src="https://img.shields.io/github/downloads/runhey/OnmyojiAutoScript/total">
    <img alt="stars" src="https://img.shields.io/github/stars/runhey/OnmyojiAutoScript?style=social">
</div>
<br>

阴阳师自动化脚本 

一键托管


### [文档](https://runhey.github.io/OnmyojiAutoScript-website/)

#### 主仓库: [https://github.com/runhey/OnmyojiAutoScript](https://github.com/runhey/OnmyojiAutoScript)

</div>

阴阳师，作为一个手游，已经进入了生命周期的晚期。从现在到关服的这段时间里，请减少花费在阴阳师上的时间，把一切都交给 OAS。

## 功能 Features

- **日常任务**: 悬赏封印、小猫咪、小杂签到、金币妖怪、年兽、花合战、地鬼、封魔、御魂整理
- **每周相关**: 真蛇、秘闻竞速、神秘商店、搜刮商店、斗技、每周小杂事
- **阴阳寮**: 结界上卡、结界蹭卡、结界突破、寮突破、狩猎战、集体任务、道馆
- **御魂副本**: 八岐大蛇、业原火、日轮之城、永生之海、六道之门
- **肝帝专属**: 探索、契灵、御灵、觉醒副本、石距、百鬼夜行
- **限时活动**: 每期爬塔、超鬼王、对弈竞猜、花车巡游、智力答题

### 显著特点 
- **全部任务**: 你能想到的没有想到的都有，一条龙给你解放双手（该画饼画饼，该挖坑挖坑）
- **无缝衔接**: 时间管理大师，优异的任务调度系统，无缝寄养，无缝执行任务
- **装饰可选**: 全部初始皮肤是什么鬼，这里支持你喜欢的庭院、主题等等，[详细说明](https://github.com/runhey/OnmyojiAutoScript/issues/180)
- **百鬼夜行**: 利用AI来智能撒豆子，模型包含所有式神，[效果展示](https://runhey.github.io/OnmyojiAutoScript-website/docs/user-manual/hyakkiyakou)

## 许可证 LICENSE

This project is licensed under the GNU General Public License v3.0.

## 声明 Announcement
本软件开源、免费，仅供学习交流使用。开发者团队拥有本项目的最终解释权。使用本软件产生的所有问题与本项目与开发者团队无关。
OAS is a free open source software, if you paid for OAS from any channel, please refund.
OAS 是一款免费开源软件，如果你在任何渠道付费购买了OAS，请退款。

## 关于 Alas
OAS 基于碧蓝航线脚本 [AzurLaneAutoScript](https://github.com/LmeSzinc/AzurLaneAutoScript) 开发，考虑到星穹铁道脚本 [StarRailCopilot](https://github.com/LmeSzinc/StarRailCopilot) 中所提及的问题，
OAS 在其基础上进行了如下优化：
- **调整设计架构**: 将前后端拆离出来更加灵活，方便后续的维护和扩展；优化代码架构使其减少同游戏耦合，更加通用。
- **搭建新的GUI**: 原先的方案过于臃肿，选用 [flutter](https://flutter.cn) 搭建一个全平台的界面端，界面更加舒适简洁
- **新的OCR库**: 跟随 [LmeSzinc](https://github.com/LmeSzinc) 的步伐， [ppocr-onnx](https://github.com/triwinds/ppocr-onnx) 更加简易使用，精度更高速度更快 
- **新的Assets管理**: 构建了一个新的Assets管理系统，更加方便的管理游戏资源如图片，文字，点击等等
- **配置文件 [pydantic](https://github.com/pydantic/pydantic) 化**: pydantic 可以更加优雅的管理用户配置

## 相关项目 Relative Repositories

- [Alas](https://github.com/LmeSzinc/AzurLaneAutoScript): 碧蓝航线的自动化脚本
- [SRC](https://github.com/LmeSzinc/StarRailCopilot): 星铁速溶茶，崩坏：星穹铁道脚本，基于下一代Alas框架。
- [OASX](https://github.com/runhey/OASX): 同 OAS 对接的全平台 GUI
- [NikkeAutoScript](https://github.com/megumiss/NIKKEAutoScript): 胜利女神：NIKKE 自动日常脚本
- [AAS](https://github.com/TheFunny/ArisuAutoSweeper): 蔚蓝档案自动化脚本
- [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights): 明日方舟小助手，全日常一键长草
- [FGO-py](https://github.com/hgjazhgj/FGO-py): 全自动免配置跨平台开箱即用的Fate/Grand Order助手
- [OAS-website](https://github.com/runhey/OnmyojiAutoScript-website): OAS 的文档网站，使用 [docusaurus](https://docusaurus.io/) 构建
- [ppocr-onnx](https://github.com/triwinds/ppocr-onnx): OCR 库，基于 onnxruntime 和 PaddleOCR
- [gurs](https://github.com/2833844911/gurs): 基于赛贝尔曲线模拟滑动轨迹, 引入其轨迹模拟人手滑动

## 联系/加入我们 Contact/Join Us



相对于其他的游戏，阴阳师玩家总体而言对脚本这类工具具有极高的排斥性。树大招风，无论你是否喜欢 OAS ，我们都希望你不在互联网上进行宣传，这保护 OAS , 也保护开发者们。

为此保持较高的入群门槛: 
#### QQ交流群: 1群465946275 2群1076565458
- 你的QQ等级必须大于32级(🌞🌞)，注册时间超过一年，低等级账号成分复杂，还请见谅。
- 你必须拥有一个 Github 账户来点一个 **Star** (这并不影响你入群后取消Star)，同样的要求注册时间过半年。
- 入群验证填入你的 Github `username`(不是`name`)，无需在意问题是什么，由QQ机器人审核(机器永远的对的)。

#### QQ开发群: 207613181 (有意开发本项目请加此群, 请不要胡乱加群)

- 开发规划：[#354](https://github.com/runhey/OnmyojiAutoScript/issues/354)
- OAS 继承了 Alas 的设计思路，极大简便了开发，欢迎提交 PR，挑选你感兴趣的部分进行开发即可。
- OAS 仍在活跃中， 我们会不定期发布未来的工作在 Issues 上并标记为 `help wanted`，欢迎向 OAS 提交 PR，我们会认真阅读你的每一行代码的。

## 安装 Installation 

- [学会提问](https://runhey.github.io/OnmyojiAutoScript-website/docs/user-manual/scientific-question): 最基本的要求，**必看必学必会**
- [用户手册](https://runhey.github.io/OnmyojiAutoScript-website/docs/user-manual/getting-started): 在线手册，不定期更新，包含所有使用说明
- [安装教程](https://runhey.github.io/OnmyojiAutoScript-website/docs/user-manual/installation): 保姆式安装手册,多翻翻有惊喜
- [开发文档](https://runhey.github.io/OnmyojiAutoScript-website/docs/development/preamble): 虽然迭代很多、年久失修，但入门开发必读，具体以源码为准

## 鸣谢 Acknowledgements

感谢所有参与到开发/测试中的朋友们

[![Contributors](https://contributors-img.web.app/image?repo=runhey/OnmyojiAutoScript)](https://github.com/runhey/OnmyojiAutoScript/graphs/contributors)

感谢所有完善文档的朋友们

[![Contributors](https://contributors-img.web.app/image?repo=runhey/OnmyojiAutoScript-website)](https://github.com/runhey/OnmyojiAutoScript-website/graphs/contributors)

<div align="center">

## Star History

[![Star History Chart](https://star-history.dera.page/svg?repos=runhey/OnmyojiAutoScript&type=Date)](https://star-history.dera.page/#runhey/OnmyojiAutoScript&Date)


## ⚡ Visitor count

![](https://profile-counter.glitch.me/runhey-OnmyojiAutoScript/count.svg)

</div>
