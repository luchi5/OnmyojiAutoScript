# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from tasks.GuildActivityMonitor.config import GuildActivityMonitor
from typing import Dict, Any

from copy import deepcopy
import json
import re
import inflection

from pathlib import Path
from pydantic import BaseModel, ValidationError, Field, PrivateAttr
from filelock import FileLock

from module.config.utils import *
from module.logger import logger

# 导入配置的Python文件
from tasks.Component.config_base import ConfigBase, TimeDelta
from tasks.Exploration.config import Exploration
from tasks.RyouToppa.config import RyouToppa
from tasks.Dokan.config import Dokan
from tasks.Script.config import Script
from tasks.Restart.config import Restart
from tasks.GlobalGame.config import GlobalGame
# 每日任务-----------------------------------------------------------------------------------------------------
from tasks.AreaBoss.config import AreaBoss
from tasks.ExperienceYoukai.config import ExperienceYoukai
from tasks.GoldYoukai.config import GoldYoukai
from tasks.Nian.config import Nian
from tasks.KekkaiUtilize.config import KekkaiUtilize
from tasks.KekkaiActivation.config import KekkaiActivation
from tasks.DemonEncounter.config import DemonEncounter
from tasks.DailyTrifles.config import DailyTrifles
from tasks.CourtyardAffairs.config import CourtyardAffairs
from tasks.TalismanPass.config import TalismanPass
from tasks.Pets.config import Pets
from tasks.SoulsTidy.config import SoulsTidy
from tasks.Delegation.config import Delegation
from tasks.WantedQuests.config import WantedQuests
from tasks.Tako.config import Tako
from tasks.AutoCheckinBigGod.config import AutoCheckinBigGod
# ----------------------------------------------------------------------------------------------------------------------
from tasks.Orochi.config import Orochi
from tasks.OrochiMoans.config import OrochiMoans
from tasks.Sougenbi.config import Sougenbi
from tasks.FallenSun.config import FallenSun
from tasks.EternitySea.config import EternitySea
from tasks.SixRealms.config import SixRealms
from tasks.RealmRaid.config import RealmRaid
from tasks.CollectiveMissions.config import CollectiveMissions
from tasks.Hunt.config import Hunt
from tasks.AbyssShadows.config import AbyssShadows
from tasks.GuildBanquet.config import GuildBanquet
from tasks.DemonRetreat.config import DemonRetreat
from tasks.GuildActivityMonitor.config import GuildActivityMonitor

# 这一部分是活动的配置-----------------------------------------------------------------------------------------------------
from tasks.ActivityShikigami.config import ActivityShikigami
from tasks.MetaDemon.config import MetaDemon
from tasks.FrogBoss.config import FrogBoss
from tasks.FloatParade.config import FloatParade
from tasks.Quiz.config import Quiz
from tasks.KittyShop.config import KittyShop
from tasks.DyeTrials.config import DyeTrials
from tasks.BudokaiTournament.config import BudokaiTournament
# ----------------------------------------------------------------------------------------------------------------------

# 肝帝专属---------------------------------------------------------------------------------------------------------------
from tasks.BondlingFairyland.config import BondlingFairyland
from tasks.EvoZone.config import EvoZone
from tasks.GoryouRealm.config import GoryouRealm
from tasks.Hyakkiyakou.config import Hyakkiyakou
from tasks.HeroTest.config import HeroTest
from tasks.FindJade.config import FindJade
from tasks.MemoryScrolls.config import MemoryScrolls
# ----------------------------------------------------------------------------------------------------------------------

# 每周任务---------------------------------------------------------------------------------------------------------------
from tasks.TrueOrochi.config import TrueOrochi
from tasks.RichMan.config import RichMan
from tasks.Secret.config import Secret
from tasks.WeeklyTrifles.config import WeeklyTrifles
from tasks.MysteryShop.config import MysteryShop
from tasks.Duel.config import Duel
from tasks.Chess.config import Chess
# ----------------------------------------------------------------------------------------------------------------------

class ConfigModel(ConfigBase):
    _save_baseline: dict = PrivateAttr(default_factory=dict)

    config_name: str = "oas"
    running_task: str = ''
    script: Script = Field(default_factory=Script)
    restart: Restart = Field(default_factory=Restart)
    global_game: GlobalGame = Field(default_factory=GlobalGame)

    # 这些是每日任务的
    area_boss: AreaBoss = Field(default_factory=AreaBoss)
    experience_youkai: ExperienceYoukai = Field(default_factory=ExperienceYoukai)
    gold_youkai: GoldYoukai = Field(default_factory=GoldYoukai)
    nian: Nian = Field(default_factory=Nian)
    realm_raid: RealmRaid = Field(default_factory=RealmRaid)
    ryou_toppa: RyouToppa = Field(default_factory=RyouToppa)
    kekkai_utilize: KekkaiUtilize = Field(default_factory=KekkaiUtilize)
    kekkai_activation: KekkaiActivation = Field(default_factory=KekkaiActivation)
    demon_encounter: DemonEncounter = Field(default_factory=DemonEncounter)
    daily_trifles: DailyTrifles = Field(default_factory=DailyTrifles)
    courtyard_affairs: CourtyardAffairs = Field(default_factory=CourtyardAffairs)
    talisman_pass: TalismanPass = Field(default_factory=TalismanPass)
    pets: Pets = Field(default_factory=Pets)
    souls_tidy: SoulsTidy = Field(default_factory=SoulsTidy)
    delegation: Delegation = Field(default_factory=Delegation)
    exploration: Exploration = Field(default_factory=Exploration)
    wanted_quests: WantedQuests = Field(default_factory=WantedQuests)
    tako: Tako = Field(default_factory=Tako)
    auto_checkin_big_god: AutoCheckinBigGod = Field(default_factory=AutoCheckinBigGod)

    # 这些是刷御魂的
    orochi: Orochi = Field(default_factory=Orochi)
    orochi_moans: OrochiMoans = Field(default_factory=OrochiMoans)
    sougenbi: Sougenbi = Field(default_factory=Sougenbi)
    fallen_sun: FallenSun = Field(default_factory=FallenSun)
    eternity_sea: EternitySea = Field(default_factory=EternitySea)
    six_realms: SixRealms = Field(default_factory=SixRealms)

    # 这些是活动的
    activity_shikigami: ActivityShikigami = Field(default_factory=ActivityShikigami)
    meta_demon: MetaDemon = Field(default_factory=MetaDemon)
    frog_boss: FrogBoss = Field(default_factory=FrogBoss)
    float_parade: FloatParade = Field(default_factory=FloatParade)
    quiz: Quiz = Field(default_factory=Quiz)
    kitty_shop: KittyShop = Field(default_factory=KittyShop)
    dye_trials: DyeTrials = Field(default_factory=DyeTrials)

    # 这些是肝帝专属
    bondling_fairyland: BondlingFairyland = Field(default_factory=BondlingFairyland)
    evo_zone: EvoZone = Field(default_factory=EvoZone)
    goryou_realm: GoryouRealm = Field(default_factory=GoryouRealm)
    hyakkiyakou: Hyakkiyakou = Field(default_factory=Hyakkiyakou)
    hero_test: HeroTest = Field(default_factory=HeroTest)
    find_jade: FindJade = Field(default_factory=FindJade)
    memory_scrolls: MemoryScrolls = Field(default_factory=MemoryScrolls)

    # 这些是每周任务
    true_orochi: TrueOrochi = Field(default_factory=TrueOrochi)
    rich_man: RichMan = Field(default_factory=RichMan)
    secret: Secret = Field(default_factory=Secret)
    weekly_trifles: WeeklyTrifles = Field(default_factory=WeeklyTrifles)
    mystery_shop: MysteryShop = Field(default_factory=MysteryShop)
    duel: Duel = Field(default_factory=Duel)
    chess: Chess = Field(default_factory=Chess)

    # 阴阳寮
    collective_missions: CollectiveMissions = Field(default_factory=CollectiveMissions)
    hunt: Hunt = Field(default_factory=Hunt)
    dokan: Dokan = Field(default_factory=Dokan)
    abyss_shadows: AbyssShadows = Field(default_factory=AbyssShadows)
    guild_banquet: GuildBanquet = Field(default_factory=GuildBanquet)
    demon_retreat: DemonRetreat = Field(default_factory=DemonRetreat)
    guild_activity_monitor: GuildActivityMonitor = Field(default_factory=GuildActivityMonitor)
    budokai_tournament: BudokaiTournament = Field(default_factory=BudokaiTournament)

    def __init__(self, config_name: str=None) -> None:
        """

        :param config_name:
        """
        if not config_name:
            super().__init__()
            self._save_baseline = deepcopy(self._serialized_data())
            return
        data = self.read_json(config_name)
        data["config_name"] = config_name
        super().__init__(**data)
        self._save_baseline = deepcopy(self._serialized_data())

    def __setattr__(self, key, value):
        """
        只要修改属性就会触发这个函数 自动保存
        :param key:
        :param value:
        :return:
        """
        super().__setattr__(key, value)
        if key.startswith('_'):
            return
        logger.info("auto save config")
        self.save()

    @staticmethod
    def read_json(config_name: str) -> dict:
        """
        读文件 没有额外操作
        :param config_name:  不带后缀
        :return:
        """
        filepath = Path.cwd() / "config" / f"{config_name}.json"
        return read_file(filepath)

    @staticmethod
    def write_json(config_name: str, data) -> None:
        """

        :param config_name: 不带后缀
        :param data:  字典而不是字符串
        :return:
        """
        filepath = Path.cwd() / "config" / f"{config_name}.json"
        write_file(filepath, data)

    def gui_args(self, task: str) -> str:
        """
        返回提供给gui显示的参数
        :param task: 输入的是任务的名称英文 如'Script' 或者是'script'都是可以的
        :return: 返回的是pydantic给我们结构化的输出的信息, 如果不能获取就返回空的str
        """
        task = convert_to_underscore(task)
        task_gui = getattr(self, task, None)
        if task_gui is None:
            logger.warning(f'{task} is no inexistence')
            return ''

        schema2 = task_gui.schema()
        # https://github.com/pydantic/pydantic/discussions/5687
        if 'definitions' in schema2:
            if 'Scheduler' in schema2['definitions']:
                if 'properties' in schema2['definitions']['Scheduler']:
                    properties = schema2['definitions']['Scheduler']['properties']
                    if 'success_interval' in properties:
                        properties['success_interval']['type'] = 'string'
                    if 'failure_interval' in properties:
                        properties['failure_interval']['type'] = 'string'
        return json.dumps(schema2)

    def gui_task(self, task: str) -> str:
        """
        返回提供给gui显示的参数
        :param task:
        :return:
        """
        task_name = convert_to_underscore(task)
        task = getattr(self, task_name, None)
        if task is None:
            logger.warning(f'{task_name} is no inexistence')
            return ''
        return task.json()

    def _serialized_data(self) -> dict:
        # Match write_file's representation, including the task serializers.
        return json.loads(json.dumps(self.model_dump(), default=str))

    @staticmethod
    def _local_changes(before, after, path=()):
        """Yield changed leaves; lists and type changes are whole values."""
        if isinstance(before, dict) and isinstance(after, dict):
            for key in before.keys() | after.keys():
                child = path + (key,)
                if key not in after:
                    yield child, True, None
                elif key not in before:
                    yield child, False, deepcopy(after[key])
                else:
                    yield from ConfigModel._local_changes(before[key], after[key], child)
        elif before != after:
            yield path, False, deepcopy(after)

    @staticmethod
    def _apply_local_changes(data: dict, changes) -> dict:
        for path, deleted, value in changes:
            parent = data
            for key in path[:-1]:
                if not isinstance(parent.get(key), dict):
                    if deleted:
                        parent = None
                        break
                    parent[key] = {}
                parent = parent[key]
            if parent is None:
                continue
            if deleted:
                parent.pop(path[-1], None)
            else:
                parent[path[-1]] = deepcopy(value)
        return data

    def save(self) -> None:
        """Merge this instance's changes without reverting another writer."""
        current = self._serialized_data()
        changes = list(self._local_changes(self._save_baseline, current))
        filepath = Path.cwd() / "config" / f"{self.config_name}.json"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        # read_file/write_file each take their own lock. Keep this entire
        # read/merge/write transaction under that same cross-process lock.
        with FileLock(f"{filepath}.lock"):
            exists = filepath.exists()
            if exists:
                with filepath.open(encoding='utf-8') as source:
                    latest = json.load(source)
                if not isinstance(latest, dict):
                    raise ValueError('Config document must be a JSON object')
                merged = self._apply_local_changes(latest, changes)
            else:
                merged = deepcopy(current)
            if changes or not exists:
                with atomic_write(filepath, overwrite=True, encoding='utf-8', newline='') as target:
                    json.dump(merged, target, indent=2, ensure_ascii=False, default=str)
            # Preserve nested objects held by the current task. External
            # values load at its normal reload boundary; old values cannot
            # become a new local change on the next save.
            self._save_baseline = deepcopy(current)

    @staticmethod
    def type(key: str) -> str:
        """
        输入模型的键值，获取这个字段对象的类型 比如输入是orochi输出是Orochi
        :param key:
        :return:
        """
        field_type: str = str(ConfigModel.__annotations__[key])
        # return field_type
        if '.' in field_type:
            classname = field_type.split('.')[-1][:-2]
            return classname
        else:
            classname = re.findall(r"'([^']*)'", field_type)[0]
            return classname

    @staticmethod
    def deep_get(obj, keys: str, default=None):
        """
        递归获取模型的值
        :param obj:
        :param keys:
        :param default:
        :return:
        """
        if not isinstance(keys, list):
            keys = keys.split('.')
        value = obj
        try:
            for key in keys:
                value = getattr(value, key)
        except AttributeError:
            return default
        return value

    @staticmethod
    def deep_set(obj, keys: str, value) -> bool:
        if not isinstance(keys, list):
            keys = keys.split('.')
        current_obj = obj
        try:
            for key in keys[:-1]:
                current_obj = getattr(current_obj, key)
            setattr(current_obj, keys[-1], value)
            return True
        except (AttributeError, KeyError):
            return False

    # ----------------------------------- fastapi -----------------------------------
    def script_task(self, task: str) -> dict:
        """

        :param task: 同gui_args函数
        :return:
        """
        task = convert_to_underscore(task)
        task = getattr(self, task, None)
        if task is None:
            logger.warning(f'{task} is no inexistence')
            return {}

        def extract_groups(sch):
            # 从schema 中提取未解析的group的数据
            # properties = properties_groups(sch)
            results = {}
            properties = {}
            for key, value in sch["properties"].items():
                if 'items' in value:
                    properties[key] = re.search(r"/([^/]+)$", value['items']['$ref']).group(1)
                else:
                    properties[key] = re.search(r"/([^/]+)$", value['$ref']).group(1)

            for key, value in properties.items():
                results[key] = sch["$defs"][value]
            return results

        def merge_value(groups, jsons, definitions) -> list[dict]:
            # 将 groups的参数，同导出的json一起合并, 用于前端显示
            result = []
            for key, value in groups["properties"].items():
                # deal with exclude 
                if key in jsons and jsons[key] == 0xABCDEF:
                    continue

                item = {}
                item["name"] = key
                item["title"] = value["title"] if "title" in value else inflection.underscore(key)
                if "description" in value:
                    item["description"] = value["description"]
                item["default"] = value["default"]
                item["value"] = jsons[key] if key in jsons else value["default"]
                item["type"] = value["type"] if "type" in value else "enum"
                if '$ref' in value:  # list
                    enum_key = re.search(r"/([^/]+)$", value['$ref']).group(1)
                    item["enumEnum"] = definitions[enum_key]["enum"]
                elif value.get('type') == 'array' and '$ref' in value.get('items', {}):
                    enum_key = value['items']['$ref'].rsplit('/', 1)[-1]
                    if 'enum' in definitions[enum_key]:
                        item['type'] = 'multi_enum'
                        item['enumEnum'] = definitions[enum_key]['enum']
                        item['minItems'] = value.get('minItems', 0)
                # if 'allOf' in value:
                #     enum_key = re.search(r"/([^/]+)$", value['allOf'][0]['$ref']).group(1)
                #     item["enumEnum"] = definitions[enum_key]["enum"]
                result.append(item)
            return result

        schema = task.model_json_schema()
        groups = extract_groups(schema)
        groups_value = groups.copy()

        result: dict[str, list] = {}
        for key, value in task.model_dump(context={'hide': True}).items():
            if key not in groups:
                for group_name in groups.keys():
                    if group_name in key:
                        groups_value[key] = groups[group_name]
            result[key] = merge_value(groups_value[key], value, schema["$defs"])

        return result

    def script_set_arg(self, task: str, group: str, argument: str, value) -> bool:
        # 验证参数
        task = convert_to_underscore(task)
        group = convert_to_underscore(group)
        argument = convert_to_underscore(argument)

        # pandtic验证
        if isinstance(value, str) and len(value) == 8:
            try:
                value = datetime.strptime(value, '%H:%M:%S').time()
            except ValueError:
                pass
        if isinstance(value, str) and len(value) == 11:
            try:
                date_time = datetime.strptime(value, '%d %H:%M:%S')
                value = TimeDelta(days=date_time.day, hours=date_time.hour, minutes=date_time.minute, seconds=date_time.second)
            except ValueError:
                pass
        if isinstance(value, str) and len(value) == 19:
            try:
                value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass
        if isinstance(value, str) and value == 'true':
            value = True
        if isinstance(value, str) and value == 'false':
            value = False

        task_object = getattr(self, task, None)
        group_object = getattr(task_object, group, None)
        if group_object is None:  # deal list
            matchs = re.findall(r'\d+', group)
            index = int(matchs[-1]) - 1 if matchs else None
            task_object_list = list(dict(task_object))
            for k, v in dict(task_object).items():
                if k not in group:
                    continue
                group_object = v[index] if group_object is None else None
        argument_object = getattr(group_object, argument, None)

        if argument_object is None:
            logger.error(f'Set arg {task}.{group}.{argument}.{value} failed')
            return False

        # XXX temp implementation to enable oasx control the datetime configuration globally rather than a single task
        if task == "restart" and group == "task_config" and argument == "reset_task_datetime_enable" and value == True:
            date_time = self.restart.task_config.reset_task_datetime
            logger.info(f"reset_task_datetime={date_time}")
            self.reset_datetime_for_all_enabled_tasks(date_time)

        # 设置参数
        try:
            setattr(group_object, argument, value)
            logger.info(f'Set arg {self.config_name}.{task}.{group}.{argument}.{value}')
            self.save()  # 我是没有想到什么方法可以使得属性改变自动保存的
            if task == 'talisman_pass':
                from tasks.Component.daily_closeout import arm_daily_closeout, request_manual_talisman
                if group == 'scheduler' and argument == 'next_run' and isinstance(value, datetime):
                    if value <= datetime.now():
                        # Only external immediate-run writes use this API.
                        # Internal task_call/save must not create manual proofs.
                        request_manual_talisman(self, value)
                elif group == 'closeout_config':
                    arm_daily_closeout(self)
            return True
        except ValidationError as e:
            logger.error(e)
            return False

    def copy_script_task(self, task_name: str, source_task: BaseModel) -> bool:
        model_task_name = convert_to_underscore(task_name)
        try:
            setattr(self, model_task_name, source_task)
            self.save()
            logger.info(f'Copy task {model_task_name} success')
            return True
        except ValidationError as e:
            logger.error(e)
            return False

    def copy_task_group(self, task_name: str, group_name: str, source_task: BaseModel) -> bool:
        model_task_name = convert_to_underscore(task_name)
        model_group_name = convert_to_underscore(group_name)
        task_object = getattr(self, model_task_name, None)
        if not task_object:
            return False
        source_group_obj = getattr(source_task, model_group_name, None)
        if not source_group_obj:
            return False
        try:
            setattr(task_object, model_group_name, source_group_obj)
            self.save()
            logger.info(f'Copy task group {model_task_name}.{model_group_name} success')
            return True
        except ValidationError as e:
            logger.error(e)
            return False

    def replace_next_run(self, d, dt: datetime):
        for k, v in d.items():
            if isinstance(v, dict):
                self.replace_next_run(v, dt=dt)
            elif k == "next_run":
                d[k] = dt
                # convert value to datetime if it's a str
                if isinstance(v, str):
                    current_time = datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
                    if current_time != dt:
                        d[k] = dt.strftime("%Y-%m-%d %H:%M:%S")
                # already a datetime value
                elif isinstance(v, datetime) and v != dt:
                    d[k] = dt.strftime("%Y-%m-%d %H:%M:%S")

    def reset_datetime_for_all_enabled_tasks(self, task_datetime: datetime):
        logger.warn(f"trying to reset datetime of all tasks to: {task_datetime}")
        # logger.info(f"current config: {self.dict()}")
        data = self.dict()
        self.replace_next_run(data, task_datetime)
        # logger.info(f"new config: {data}")

        # Keep bulk schedule changes on the same merge path as normal saves.
        baseline = deepcopy(self._save_baseline)
        super().__init__(**data)
        self._save_baseline = baseline
        self.save()


if __name__ == "__main__":
    try:
        c = ConfigModel("oas1")
    except ValidationError as e:
        print(e)
        c = ConfigModel()

    print(c.script_task('GuildBanquet'))

