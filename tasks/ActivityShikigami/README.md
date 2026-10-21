## 当前适配：2026 年 10 月「拾此一瞬」

- 活动门票 `pass` 对应「磐长故地」，不是「古迹演武」的体力副本。
- 从活动总览的版本活动进入，经过磐长故地地图的「战场攻占」进入挑战页。
- 每个账号在「通用爬塔 → 活动门票难度」选择「保持当前」「简单（1张门票）」「困难（5张门票）」。旧配置默认保持当前。指定难度时先点击切换，再连续确认实际消耗；未确认则不挑战。
- 使用原有门票挑战次数、运行总时间、御魂切换和预设配置。启用预设时解除阵容锁定，否则锁定当前阵容。
- 门票余额和每场消耗连续识别一致后才挑战；简单不足 1 张、困难不足 5 张正常结束，识别失败明确报错，不按 0 处理。
- 每场只点击一次挑战。确认胜利结算和返回挑战页后计一次，下一场前重新确认余额；挑战次数设置仍按场数计算。失败、难度意外变化或页面异常停止，不反复消耗门票。
- 二阶段首领战沿用已有 `boss` 模式，从活动总览「神降尘世」进入，直接使用右下角挑战。只运行首领战时把运行顺序设为 `boss`；先门票后首领设为 `pass,boss`。原有顺序中包含 `boss` 的账号也会执行首领战，请核对后再启动。
- 「通用爬塔 → 首领战计数方式」默认「自动补满100次奖励」。也可选择「指定场次（忽略奖励数字）」，填写「首领战指定场次」（原 `boss_limit`）；指定模式完全不读取奖励数字或自动进度检查点，只按本次任务的实际胜利场数运行，0场直接跳过。指定模式下即使奖励已满仍按用户设定执行；重新启动任务会重新计数。模式修改后需重新启动该账号任务，新增配置字段需后端重新加载后显示。
- 自动模式以右下数字为主、左下括号进度为备用；每次最多尝试3秒。读不清、两处冲突或数字倒退时记为未知，继续挑战。同一挑战页连续两次读到相同值后采信；100/100若仅一处可读，须连续3张新截图确认。可靠读数加上之后实际胜场计至100即结束，不要求再次读到100；例如确认24后再赢76场，确认90后再赢10场。没有可靠读数也继续打，但本次最多100场胜利。后续较低或不变的读数不能延长剩余场数。
- BOSS每局只点一次挑战，复用原有准备、预设、绿标和结算流程；每场胜利后直接回到BOSS挑战页，本次胜利场数照常加1。奖励数字可以暂时不变或在恢复识别后一次跳过多场，不再要求逐局恰好加1。挑战失败或未返回挑战页仍停止。不能将误读的717100直接修补为71/100。
- 本首领战可能持续超过5分钟；仅在该战斗等待期间把设备长等待上限设为10分钟，结束或异常退出后恢复原值。保留超时与异地登录检测。
- 自动进度保存在 `data/ActivityShikigami/event_202610_boss/`，按账号隔离、原子写入，分别记录已确认读数与加上实际胜场后的计数；兼容旧检查点。识别失败或重启不会丢掉已计入的胜场，未知读数也不会伪装成0。固定场次模式不读取或修改自动模式检查点。
- 自动模式忽略旧 `boss_limit`（默认20次），指定场次模式使用它。两种模式均保留运行总时间限制；预计5–6小时的订单请设足总时间（例如7小时），到达时间上限时按未完成排期。御魂和预设仍使用原有BOSS配置。
- `ap`、`ap100` 仍跳过。本期素材集中在 `event_202610`，门票逻辑在 `current_event.py`，BOSS逻辑在 `current_boss.py`，导航在 `page.py`。

离线回归：`toolkit\python.exe -m unittest dev_tools.test_activity_ticket_event`。
BOSS回归：`toolkit\python.exe -m unittest dev_tools.test_activity_boss_event dev_tools.test_boss_progress_store`。已用用户截图及1号实机截图核验OCR（65、66、71）。2026-10-20实测首场65→66，确认数字为已完成次数；修正方向后再次实测66→67，胜利结算、直接回BOSS挑战页、自动下一场均已走通。71处出现过中文整段OCR将斜杠误读为7的问题，缩小数字区域及左下备用区域后，真实截图回归均通过；16:59重启新版后双方位置均识别71，17:02:33实测71→72，检查点更新为72，17:02:35自动开始下一场。100/100停止边界已通过离线回归，尚未实际打到100验收。
已使用 1 号困难、当前阵容采集一次完整实机流程：235 -> 230 张，奖励页返回磐长故地；新导航已实际从庭院进入门票挑战页，确认剩余 230 张。连续自动挑战与其他账号仍需实际验收。
已实测使用新代码双向切换简单 / 困难，分别识别消耗 1 / 5，切换测试未开始战斗且门票仍为 230；结束时恢复原来的困难。简单模式战斗结算仍待实机验收。

---

## 这里是约定哪些是需要更新的     ./script_test.py 是对应的测试



## 关于页面导航（`./as/page.json`文件）

#### 图片 `shi` 表示进入到活动的主页面，截图的时候往上截图一点，因为快结束的时候图片下方会显示还有多少小时结束

![image-20251220103009134](https://runhey-img-stg1.oss-cn-chengdu.aliyuncs.com/img3/202512201030265.png)

#### 图片`to_battle_main` 和 `to_battle_boss` 分别表示进入默认的挑战界面和boss挑战界面

![image-20251220103500219](https://runhey-img-stg1.oss-cn-chengdu.aliyuncs.com/img3/202512201035354.png)

#### 图片`check_battle_main` 表示到达了这个默认战斗界面       |  `battle_main_to_records`表示从这里进入式神录来切换御魂

#### 图片`check_battle_boss`  和 `battle_boss_to_records` 类似的

![image-20251220104003594](https://runhey-img-stg1.oss-cn-chengdu.aliyuncs.com/img3/202512201040758.png)

#### 可能需要更新的如 `back_green`、 `red_exit` 和 `skip_button`  这些表示退出的时候碰到的时候会出现的

#### OAS 内部有一个专门的类来存放通用的图片。  路径在`./tasks/GlobalGame/ui`  有需要直接使用

![image-20251220104718877](https://runhey-img-stg1.oss-cn-chengdu.aliyuncs.com/img3/202512201047907.png)

## 关于活动切换（`./as/image.json`文件）

#### 图片 `climb_mode_pass` 门票爬塔标志 | `climb_mode_ap` 体力爬塔标志 | `climb_mode_switch` 切换门票爬塔和体力爬塔按钮

![climb_mode_pass](https://raw.githubusercontent.com/AzurTian/ImgBed/master/blog/climb_mode_pass.png)

![climb_mode_ap](https://raw.githubusercontent.com/AzurTian/ImgBed/master/blog/1.png)

![climb_mode_switch](https://raw.githubusercontent.com/AzurTian/ImgBed/master/blog/2.png)


## 关于战斗更新 （`./fire/ocr.json`文件）

#### OCR `remain_ap`  表示体力模式下剩余多少次数  `fire` 表示点击挑战

![image-20251220111306772](https://runhey-img-stg1.oss-cn-chengdu.aliyuncs.com/img3/202512201113931.png)

活动币模式下 `remain_pass`  和 boss 模式下也是类似

![image-20251220111505769](https://runhey-img-stg1.oss-cn-chengdu.aliyuncs.com/img3/202512201115927.png)

