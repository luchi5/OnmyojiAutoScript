# 云景阆苑庭院识别

`main13_check_main_13_stairs.png` 和对应识别区域沿用
[xylolit-mu/OnmyojiAutoScript](https://github.com/xylolit-mu/OnmyojiAutoScript)
的 `tasks/Component/Costume/main13/main13_check_main_13.png`，保留项目 GPLv3 许可。
阶梯区域比原来的小块背景稳定，仍使用 0.80 的识别阈值。

新模板使用独立文件名。保留原背景模板，避免已加载旧规则的运行中进程
读取到尺寸不同的图片；已有进程下次启动后加载新规则。
