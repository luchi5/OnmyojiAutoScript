## 当前适配：2026 年 10 月「拾此一瞬」

- 活动门票 `pass` 对应「磐长故地」，不是「古迹演武」的体力副本。
- 从活动总览的版本活动进入，经过磐长故地地图的「战场攻占」进入挑战页。
- 每个账号在「通用爬塔 → 活动门票难度」选择「保持当前」「简单（1张门票）」「困难（5张门票）」。旧配置默认保持当前。指定难度时先点击切换，再连续确认实际消耗；未确认则不挑战。
- 使用原有门票挑战次数、运行总时间、御魂切换和预设配置。启用预设时解除阵容锁定，否则锁定当前阵容。
- 门票余额和每场消耗连续识别一致后才挑战；简单不足 1 张、困难不足 5 张正常结束，识别失败明确报错，不按 0 处理。
- 每场只点击一次挑战。确认奖励结算、返回挑战页和扣除对应的 1 / 5 张后计一次；挑战次数设置仍按场数计算。失败、难度意外变化或页面异常停止，不反复消耗门票。
- 当前仅适配活动门票。运行顺序里遗留的 `ap`、`ap100`、`boss` 会记录提示并跳过；不会自动进入其他资源副本。
- 本期入口和资源位置集中在 `current_event.py` / `event_202610`，导航在 `page.py`。下面的旧通用素材说明仅供下一期维护参考。

离线回归：`toolkit\python.exe -m unittest dev_tools.test_activity_ticket_event`。
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

