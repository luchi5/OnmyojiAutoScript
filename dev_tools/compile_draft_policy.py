"""Compile user spreadsheet into explicit runtime tables, without network/game IO."""
import hashlib
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "tasks/DraftDuel"

# Explicit role vocabulary, applied at build time only.
WORDS = {
    "输出": "damage", "间伤": "damage", "火机": "fire",
    "奶": "heal", "治疗": "heal", "盾": "shield",
    "控制": "control", "拉条": "pull", "推条": "push",
    "驱散": "dispel", "解控": "dispel", "保人": "protect",
    "保单核": "protect", "挡单体": "protect", "抗单体": "protect",
    "减伤": "protect", "抵抗": "resist", "肉坦": "protect",
    "锁伤": "protect", "增伤": "buff", "加攻": "buff",
    "复活": "revive", "普攻": "basic_support", "免疫": "protect",
    "反弹": "reflect",
    "辅助": "support",
}


def compile_policy():
    source = BASE / "ratings.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    profiles = {}
    for name, p in raw["profiles"].items():
        row = p["source"].split("E")[-1]
        cells = p["cells"]
        roles = {tag for word, tag in WORDS.items() if word in p["role"]}
        if "群" in p["role"] and "damage" in roles:
            roles.add("aoe")
        if "单体" in p["role"] and "damage" in roles:
            roles.add("single_damage")
        flags = list(p["flags"])
        if p["role"].strip("？? ") == "":
            flags.append("unknown_role")
        profiles[name] = dict(
            strength=p["strength"], roles=sorted(roles), source=p["source"],
            role_source="评分总表!D"+row, flags=flags,
            notes={key: cells.get(col+row, "") for key, col in
                   (("evaluation", "J"), ("partners", "K"), ("weakness", "M"),
                    ("avoid", "N"), ("soul_recommendation", "O"))},
            conditional_tags=[], numeric_conditions=[],
            selection_status="scored" if p["strength"] is not None else "fallback_unrated")

    rules = []
    def tag(name):
        if name not in profiles:
            raise ValueError("Unknown rule member: "+name)
        return "name:"+name
    def members(*names):
        return [tag(n) for n in names]
    def rule(key, groups, weight, source, reason):
        rules.append(dict(key=key, groups=groups, weight=weight,
                          source=source, reason=reason))

    # B-column membership is interpreted here, never from arbitrary prose at runtime.
    combo_specs = [
        (2, [members("云间不见岳"), members("炼狱茨木童子", "烬天玉藻前"), members("福悦座敷童子")]),
        (3, [members("鬼王酒吞童子"), members("禅心云外镜"), ["aoe"]]),
        (4, [members("浮世青行灯"), members("心狩鬼女红叶"), members("阎魔", "古笼火")]),
        (5, [members("食灵"), members("姑获鸟"), members("鬼童丸", "心友犬神")]),
        (6, [members("空相面灵气"), members("帝释天", "纺愿缘结神"), members("大夜摩天阎魔")]),
        (7, [members("龙珏"), members("歌留多"), members("荒骷髅", "入内雀")]),
        (8, [members("不知火"), members("酒吞童子", "吸血姬"), members("椒图")]),
        (9, [members("鲸汐千姬"), members("初音未来"), ["observed:aoe_haiyue"]]),
        (10, [members("季"), members("犬神"), members("虫师", "梦引蝴蝶精")]),
        (11, [members("一目连", "鲤鱼精"), members("匣中少女"), members("日和坊", "虫师")]),
        (12, [members("蚀月吸血姬"), members("阎魔"), ["single_damage"]]),
        (13, [members("妖刀姬绯夜猎刃", "犬神", "吸血姬"), members("椒图"), members("白藏主")]),
        (14, [members("神启荒"), members("大夜摩天阎魔", "遥念烟烟罗"), ["buff"]]),
        # Row15 conflicts with its own warning about summon slots: audit only.
        (16, [members("镰鼬"), members("孟婆", "星熊童子"), ["observed:fast_aoe"]]),
        (17, [members("言灵"), members("鬼金羊"), ["aoe"]]),
        (18, [members("雨女", "垢尝", "数珠"), ["damage"], ["fire"]]),
        (19, [members("入内雀"), members("荒骷髅"), members("龙珏", "雪御前")]),
    ]
    combo_audit = []
    for cells in raw["combinations"]:
        row = next(int(k[1:]) for k in cells if k.startswith("A"))
        spec = next((groups for r, groups in combo_specs if r == row), None)
        combo_audit.append(dict(row=row, name=cells["A"+str(row)],
                               status="compiled" if spec else "held_conflicting_summon_advice"))
        if spec:
            # Policy units, not a claimed win rate or extra copy of composite H.
            weight = round((float(cells["D"+str(row)])-80)/5, 2)
            rule("combo_"+str(row), spec, weight, "组合推荐!B%d:F%d" % (row, row),
                 cells["A"+str(row)]+"：完整组合加分（资料启发式）")
    # Unambiguous numeric conditions. Base E is preserved, explicit J overrides it.
    for n in ("福悦座敷童子", "炼狱茨木童子", "烬天玉藻前"):
        profiles[n]["strength"] = 70
        rule("numeric_"+n, [members(n), members("云间不见岳")], 2,
             profiles[n]["source"].replace("!E", "!J"), n+"：有云间不见岳时70→90")
    profiles["禅心云外镜"]["strength"] = 80
    rule("numeric_禅心云外镜", [members("禅心云外镜"), members("鬼王酒吞童子")], 1.5,
         profiles["禅心云外镜"]["source"].replace("!E", "!J"), "禅心云外镜：有鬼王酒吞童子时80→95")

    # Named ally conflicts only. Enemy counters and ambiguous nicknames are notes.
    conflicts = [
        ("帝释天", "纺愿缘结神"), ("纺愿缘结神", "稻荷神御馔津"),
        *[(n, "禅心云外镜") for n in ("晨辉惠比寿", "纺愿缘结神", "蜜桃芥子",
                                     "绘世花鸟卷", "夏目猫老师", "聆海金鱼姬")],
        *[(n, other) for n in ("桃花妖", "日和坊", "蝴蝶精")
          for other in ("季", "云外镜", "镜音铃连")],
    ]
    for a, b in conflicts:
        rule("avoid_"+a+"_"+b, [members(a), members(b)], -3,
             profiles[a]["source"].replace("!E", "!N"), a+"与"+b+"：表内同队避坑扣分")
    summon_names = [n for n, p in profiles.items() if "召唤物式神冲突" in p["notes"]["avoid"]]
    for index, a in enumerate(summon_names):
        for b in summon_names[index+1:]:
            rule("summon_"+a+"_"+b, [members(a), members(b)], -2,
                 profiles[a]["source"].replace("!E", "!N"), "资料提示召唤位冲突："+a+" / "+b)

    # Soul bonuses require observed tags, never the sheet's recommended soul.
    profiles["心狩鬼女红叶"]["conditional_tags"].append(
        dict(required_soul="counterattack", tag="momiji_counterattack"))
    profiles["椒图"]["roles"].append("jiaotu")
    for n, p in profiles.items():
        if "aoe" in p["roles"]:
            p["conditional_tags"] += [
                dict(required_soul="海月火玉", tag="observed:aoe_haiyue"),
                dict(min_speed=220, tag="observed:fast_aoe")]
        p["roles"].append(tag(n))
        p["rule_keys"] = [r["key"] for r in rules if any(tag(n) in g for g in r["groups"])]
        note = p["notes"]["evaluation"]
        # Exact numeric cases above are implemented. Other qualifiers stay visible
        # as review limitations, rather than becoming invented tier-to-score rules.
        if any(word in note for word in ("有", "没有", "速", "/", "前", "后", "单上", "队")):
            if n not in ("福悦座敷童子", "炼狱茨木童子", "烬天玉藻前", "禅心云外镜"):
                p["flags"].append("qualitative_condition_base_only")
    return dict(schema_version=1, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                aliases={"萤草": "莹草"}, profiles=profiles, groups=rules,
                combo_audit=combo_audit,
                policy=dict(strength_scale=10, redundant_fire_penalty=1.5,
                            note="启发式选将权重，不代表胜率；未知条件不加分"))


if __name__ == "__main__":
    compiled = compile_policy()
    (BASE / "selection_policy.json").write_text(
        json.dumps(compiled, ensure_ascii=False, indent=2), encoding="utf-8")
    with (BASE / "选将权重明细.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["式神", "基础强度", "职责标签", "状态", "待核验说明", "规则数", "来源"])
        for name, profile in compiled["profiles"].items():
            writer.writerow([name, profile["strength"], "|".join(profile["roles"]),
                             profile["selection_status"], "|".join(profile["flags"]),
                             len(profile["rule_keys"]), profile["source"]])
    summary = {status: sum(p["selection_status"] == status for p in compiled["profiles"].values())
               for status in ("scored", "fallback_unrated")}
    print(json.dumps(dict(profiles=len(compiled["profiles"]), groups=len(compiled["groups"]),
                          status=summary), ensure_ascii=False))
