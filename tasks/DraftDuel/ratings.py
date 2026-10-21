"""Conservative spreadsheet annotations; combination prose is never executable."""
import json
from dataclasses import replace
from pathlib import Path

from functools import lru_cache
from tasks.DraftDuel.strategy import StrategyPolicy, Synergy, GroupRule


@lru_cache(maxsize=1)
def compiled_policy():
    data = json.loads(Path(__file__).with_name("selection_policy.json").read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported selection policy")
    return data


class Ratings:
    def __init__(self, path=None):
        self.data = (json.loads(Path(path).read_text(encoding="utf-8"))
                     if path else compiled_policy())
        self.profiles = dict(self.data["profiles"])
        # Live screenshot says 萤草; workbook C column has the typo 莹草.
        self.profiles["萤草"] = self.profiles["莹草"]

    def annotate(self, candidate):
        profile = self.profiles.get(candidate.name)
        if profile is None:
            return candidate
        tags = set(profile["roles"])
        for condition in profile["conditional_tags"]:
            if "required_soul" in condition and condition["required_soul"] not in candidate.soul_tags:
                continue
            if "min_speed" in condition and (candidate.stats.speed is None or
                    candidate.stats.speed < condition["min_speed"]):
                continue
            tags.add(condition["tag"])
        # Unrated entries remain unscored and visibly low-confidence.
        confidence = candidate.confidence if profile["strength"] is not None else min(candidate.confidence, .25)
        return replace(candidate, roles=candidate.roles | tags,
                       strength=profile["strength"], strength_source=profile["source"],
                       confidence=confidence)


@lru_cache(maxsize=1)
def rating_policy():
    base = StrategyPolicy()
    data = compiled_policy()
    return replace(base,
                   coverage=base.coverage + (("protect", 1.5), ("resist", .5), ("buff", .5)),
                   groups=tuple(GroupRule(r["key"], tuple(frozenset(g) for g in r["groups"]),
                                          r["weight"], r["reason"]+" ["+r["source"]+"]")
                                for r in data["groups"]),
                   redundant_fire_penalty=data["policy"]["redundant_fire_penalty"],
                   synergies=base.synergies + (
        Synergy("jiaotu", "momiji_counterattack", 4,
                "用户确认组合：椒图 + 已确认反击御魂的心狩鬼女红叶"),))
