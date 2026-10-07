# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

import cv2

from module.atom.image import RuleImage
from module.atom.gif import RuleGif
from module.logger import logger
from module.server.i18n import I18n

from tasks.Component.Costume.config import (
    MainType, CostumeConfig, RealmType,
    ThemeType, ShikigamiType, SignType,
    BattleType, BattleSceneType, CarpBannerType
)
from tasks.Component.Costume.assets import CostumeAssets
from tasks.Component.CostumeBattle.assets import CostumeBattleAssets
from tasks.Component.CostumeShikigami.assets import CostumeShikigamiAssets
from tasks.Component.CostumeCarpBanner.assets import CostumeCarpBannerAssets
from tasks.Component.CostumeBattleScene.assets import CostumeBattleSceneAssets
from tasks.Component.Costume.image_replacement import replace_image_asset

# 庭院皮肤
# 主界面皮肤（使用字典推导式动态生成）
main_costume_model = {
    getattr(MainType, f"COSTUME_MAIN_{i}"): {
        'I_CHECK_MAIN': f'I_CHECK_MAIN_{i}',
        'I_MAIN_GOTO_EXPLORATION': f'I_MAIN_GOTO_EXPLORATION_{i}',
        'I_MAIN_GOTO_SUMMON': f'I_MAIN_GOTO_SUMMON_{i}',
        'I_MAIN_GOTO_TOWN': f'I_MAIN_GOTO_TOWN_{i}',
        'I_PET_HOUSE': f'I_PET_HOUSE_{i}'
    } for i in range(1, 17)
}
# 玉岚狐庭（issue #1824）
main_costume_model[getattr(MainType, "COSTUME_MAIN_17")] = {
    'I_CHECK_MAIN': ['I_CHECK_MAIN_17_A', 'I_CHECK_MAIN_17_B', 'I_CHECK_MAIN_17_C'],
    'I_MAIN_GOTO_EXPLORATION': ['I_MAIN_GOTO_EXPLORATION_17_A', 'I_MAIN_GOTO_EXPLORATION_17_B', 'I_MAIN_GOTO_EXPLORATION_17_C'],
    'I_MAIN_GOTO_SUMMON': ['I_MAIN_GOTO_SUMMON_17_A', 'I_MAIN_GOTO_SUMMON_17_B', 'I_MAIN_GOTO_SUMMON_17_C'],
    'I_MAIN_GOTO_TOWN': ['I_MAIN_GOTO_TOWN_17_A', 'I_MAIN_GOTO_TOWN_17_B', 'I_MAIN_GOTO_TOWN_17_C'],
    'I_PET_HOUSE': ['I_PET_HOUSE_17_A', 'I_PET_HOUSE_17_B', 'I_PET_HOUSE_17_C'],
}
main_costume_model[getattr(MainType, "COSTUME_MAIN_13")] = {
    'I_CHECK_MAIN': ['I_CHECK_MAIN_13',],
    'I_MAIN_GOTO_EXPLORATION': ['I_MAIN_GOTO_EXPLORATION_13', ],
    'I_MAIN_GOTO_SUMMON': ['I_MAIN_GOTO_SUMMON_13',],
    'I_MAIN_GOTO_TOWN': ['I_MAIN_GOTO_TOWN_13',],
    'I_PET_HOUSE': ['I_PET_HOUSE_13',],
}



# 鲤鱼旗皮肤
carpbanner_costume_model = {
    getattr(CarpBannerType, f"COSTUME_CARPBANNER_{i}"): {
        'I_SHI_CARD': f'I_SHI_CARD_{i}',
        'I_SHI_DEFENSE': f'I_SHI_DEFENSE_{i}',
        'I_SHI_GROWN': f'I_SHI_GROWN_{i}',
    } for i in range(1, 4)
}


# 战斗主题
battle_theme_model = {
    getattr(BattleType, f"COSTUME_BATTLE_{i}"): {
        'I_LOCAL': f'I_LOCAL_{i}',
        'I_EXIT': f'I_EXIT_{i}',
        'I_FRIENDS': f'I_FRIENDS_{i}',
        'I_BATTLE_INFO': f'I_BATTLE_INFO_{i}',
        # 以下资源并非所有主题都需要修改，未采集的资源将被跳过
        'I_WIN': f'I_WIN_{i}', # 已知：8，12，13，14
        'I_DE_WIN': f'I_DE_WIN_{i}', # 已知：8，12，13，14
        'I_FALSE': f'I_FALSE_{i}' # 已知：8，12，13，14
    } for i in range(1, 16)
}

# 战斗场景皮肤。图片资源待补充，先保留空映射。
battle_scene_model = {
    BattleSceneType.COSTUME_BATTLE_SCENE_1: {
        'I_REWARD': 'I_REWARD_1',
    },
}

# 幕间主题
shikigami_costume_model = {
    getattr(ShikigamiType, f"COSTUME_SHIKIGAMI_{i}"): {
        # GameUi 进出式神录
        'I_CHECK_RECORDS': f'I_CHECK_RECORDS_{i}',
        'I_RECORD_SOUL_BACK': f'I_RECORD_SOUL_BACK_{i}',
        # SwitchSoul 相关
        'I_SOUL_PRESET': f'I_SOUL_PRESET_{i}',
        'I_SOU_CHECK_IN': f'I_SOU_CHECK_IN_{i}',
        'I_SOU_TEAM_PRESENT': f'I_SOU_TEAM_PRESENT_{i}',
        'I_SOU_CLICK_PRESENT': f'I_SOU_CLICK_PRESENT_{i}',
        'I_SOU_SWITCH_SURE': f'I_SOU_SWITCH_SURE_{i}',
        # SwitchSoul 分组相关 (1-7组)
        **{f'I_SOU_CHECK_GROUP_{g}': f'I_SOU_CHECK_GROUP_{g}_{i}' for g in range(1, 8)},
        # SwitchSoul 队伍相关 (1-4队)
        **{f'I_SOU_SWITCH_{t}': f'I_SOU_SWITCH_{t}_{i}' for t in range(1, 5)},
        # SoulsTidy 相关
        'I_ST_SOULS': f'I_ST_SOULS_{i}',
        'I_ST_REPLACE': f'I_ST_REPLACE_{i}',
    }
    for i in range(1, 13)  # 目前支持 COSTUME_SHIKIGAMI_1 到 COSTUME_SHIKIGAMI_12
}

# RuleImage objects are also held by navigation pages. Restore them in place,
# including any methods attached by RuleGif, rather than replacing references.
_default_main_assets = {}
_main_asset_names = tuple(main_costume_model[MainType.COSTUME_MAIN_1])


def _clone_image(rule):
    return RuleImage(tuple(rule.roi_front), tuple(rule.roi_back),
                     rule.method, rule.threshold, rule.file)

class CostumeBase:
    def check_costume(self, config: CostumeConfig=None):
        if config is None:
            config: CostumeConfig = self.config.model.global_game.costume_config
        current_task = self.get_task_name()
        logger.info(f'Current task: {current_task}')
        self.check_costume_main(config.costume_main_type)
        self.check_costume_carpbanner(config.costume_carpbanner_type)
        self.check_costume_battle(config.costume_battle_type)
        self.check_costume_battle_scene(config.costume_battle_scene_type)
        self.check_costume_shikigami(config.costume_shikigami_type)

    def replace_img(self,
                    asset_before: str,
                    asset_after: RuleImage,
                    rp_roi_back: bool = True):
        replace_image_asset(self, asset_before, asset_after, rp_roi_back)

    def check_costume_main(self, main_types):
        selected = CostumeConfig(costume_main_type=main_types).costume_main_type
        for key in _main_asset_names:
            if key not in _default_main_assets and hasattr(self, key):
                _default_main_assets[key] = _clone_image(getattr(self, key))
        self.main_costume_candidates = selected
        self._main_candidate_checks = {}
        self._activate_main_costume(selected[0])
        if len(selected) > 1:
            logger.info('Auto-detect main costume from: ' + ', '.join(item.value for item in selected))

    def _activate_main_costume(self, main_type):
        self._main_detection_cache = None
        for key, original in _default_main_assets.items():
            if not hasattr(self, key):
                continue
            target = getattr(self, key)
            target.__dict__.clear()
            target.__dict__.update(_clone_image(original).__dict__)
            # Keep identity/hash and interval timer keys stable across skins.
            target.name = original.name
        for key, value in main_costume_model.get(main_type, {}).items():
            if not hasattr(self, key):
                continue
            target = getattr(self, key)
            stable_name = target.name
            if isinstance(value, list):
                rules = [_clone_image(getattr(CostumeAssets, item)) for item in value]
                RuleGif.attach_to(target, rules)
            else:
                rule = _clone_image(getattr(CostumeAssets, value))
                target.__dict__.update(rule.__dict__)
            target.name = stable_name
        self.current_main_type = main_type
        logger.info(f'Switch main costume to {main_type.value} ({I18n.trans_zh_cn(main_type)})')

    def _main_candidate_score(self, rule, image, threshold=None):
        """Compare courtyard templates without lowering their recognition thresholds."""
        best_score = None
        frames = rule.targets if isinstance(rule, RuleGif) else (rule,)
        for frame in frames:
            if not frame.is_template_match:
                score = 1.0 if frame.match(image, threshold=threshold) else None
            else:
                source, template = frame.corp(image), frame.image
                if (source.shape[0] < template.shape[0]
                        or source.shape[1] < template.shape[1]):
                    continue
                result = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
                score = cv2.minMaxLoc(result)[1]
                limit = frame.threshold if threshold is None else threshold
                if not score > limit:
                    continue
            if score is not None and (best_score is None or score > best_score):
                best_score = score
        return best_score

    def detect_random_main_costume(self, threshold=None):
        candidates = tuple(getattr(self, 'main_costume_candidates', ()))
        if len(candidates) < 2:
            return False
        image = self.device.image
        cached = getattr(self, '_main_detection_cache', None)
        if (cached is not None and cached[0] is image
                and cached[1] == threshold and cached[2] == candidates):
            return cached[3]
        best_type, best_score = None, None
        for main_type in candidates:
            if main_type not in self._main_candidate_checks:
                if main_type == MainType.COSTUME_MAIN:
                    rule = _clone_image(_default_main_assets['I_CHECK_MAIN'])
                else:
                    asset = main_costume_model[main_type]['I_CHECK_MAIN']
                    rule = (RuleGif([_clone_image(getattr(CostumeAssets, name)) for name in asset])
                            if isinstance(asset, list) else _clone_image(getattr(CostumeAssets, asset)))
                self._main_candidate_checks[main_type] = rule
            score = self._main_candidate_score(self._main_candidate_checks[main_type], image, threshold)
            if score is not None and (best_score is None or score > best_score
                                      or (score == best_score and main_type == self.current_main_type)):
                best_type, best_score = main_type, score
        matched = False
        if best_type is not None:
            if best_type != self.current_main_type:
                self._activate_main_costume(best_type)
                logger.info(f'Main costume best match: {best_type.value}, score={best_score:.4f}')
            # Populate the active rule's matched coordinates on this frame.
            matched = self.I_CHECK_MAIN.match(image, threshold=threshold)
        # Keep the image reference so a recycled Python id cannot reuse stale results.
        self._main_detection_cache = (image, threshold, candidates, matched)
        return matched

    def check_costume_carpbanner(self, carpbanner_type: CarpBannerType):
        if carpbanner_type == CarpBannerType.COSTUME_CARPBANNER_DEFAULT:
            return
        logger.info(f'Switch carp banner theme {carpbanner_type} (override realm assets) ({I18n.trans_zh_cn(carpbanner_type)})')
        carpbanner_assets = CostumeCarpBannerAssets()
        model = carpbanner_costume_model.get(carpbanner_type, {})
        for key, value in model.items():
            if not hasattr(carpbanner_assets, value):
                logger.warning(f'Carp banner asset {value} not found, skip')
                continue
            assert_value: RuleImage = getattr(carpbanner_assets, value)
            # 执行替换（覆盖结界皮肤的同名key）
            self.replace_img(key, assert_value)

    def check_costume_battle(self, battle_type: BattleType):
        if battle_type == BattleType.COSTUME_BATTLE_DEFAULT:
            return
        logger.info(f'Switch battle theme {battle_type} ({I18n.trans_zh_cn(battle_type)})')
        costume_battle_assets = CostumeBattleAssets()
        for key, value in battle_theme_model[battle_type].items():
            if not hasattr(costume_battle_assets, value):
                # 尚未采集完成的资产，跳过
                continue
            assert_value: RuleImage = getattr(costume_battle_assets, value)
            # 绿标的坐标点范围不变
            if key == 'I_LOCAL':
                self.replace_img(key, assert_value, rp_roi_back=False)
            else:
                self.replace_img(key, assert_value)

    def check_costume_battle_scene(self, scene_type: BattleSceneType):
        if scene_type == BattleSceneType.COSTUME_BATTLE_SCENE_DEFAULT:
            return
        logger.info(f'Switch battle scene skin {scene_type} ({I18n.trans_zh_cn(scene_type)})')
        current_task = self.get_task_name()
        allowed_tasks = {
            'Orochi',
        }
        if current_task not in allowed_tasks:
            return

        costume_assets = CostumeBattleSceneAssets()
        for key, value in battle_scene_model.get(scene_type, {}).items():
            if not hasattr(costume_assets, value):
                # 尚未采集完成的资产，跳过
                continue
            assert_value: RuleImage = getattr(costume_assets, value)
            self.replace_img(key, assert_value)

    def check_costume_shikigami(self, shikigami_type: ShikigamiType):
        if shikigami_type == ShikigamiType.COSTUME_SHIKIGAMI_DEFAULT:
            return
        logger.info(f'Switch shikigami theme {shikigami_type} ({I18n.trans_zh_cn(shikigami_type)})')
        shikigami_assets = CostumeShikigamiAssets()
        model = shikigami_costume_model.get(shikigami_type, {})
        for key, value in model.items():
            if not hasattr(shikigami_assets, value):
                # 尚未采集完成的资产，跳过
                continue
            assert_value: RuleImage = getattr(shikigami_assets, value)
            # 一般不需要固定 back ROI，如确有需要可在此为特例设置 rp_roi_back=False
            self.replace_img(key, assert_value)


if __name__ == '__main__':
    c = CostumeBase()
    c.check_costume_main(MainType.COSTUME_MAIN_13)
