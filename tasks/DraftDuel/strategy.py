"""Offline, explainable Draft Duel heuristics; no game IO or win-rate claims.

Role tags understood by the default policy: damage, fire, heal, shield, control,
dispel, pull, heavy_fire. Soul tags are observations, not invented stat bonuses.
Both can participate in caller-supplied synergy rules. Optional strength scores
must carry their source. Stat scales are scoring units, not game attributes.

A readable optional role catalogue is JSON, for example::

    {"schema_version": 1, "version": "reviewed-2026-10-18", "profiles": {
      "Example": {"roles": ["fire"], "confidence": 0.8,
                  "source": "manually verified skill description"}}}

Catalogue annotations are only as reliable as the recognised identity and
their cited profile. ratings.py separately loads the user's spreadsheet.
"""
from dataclasses import dataclass, field, fields, replace
from itertools import combinations, product
import json
from math import isfinite
from pathlib import Path
from typing import FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple


def _number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("{} must be a finite number".format(label))
    if minimum is not None and value < minimum:
        raise ValueError("{} is below its minimum".format(label))
    if maximum is not None and value > maximum:
        raise ValueError("{} is above its maximum".format(label))


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a nonempty string".format(label))


def _tags(values):
    if isinstance(values, str):
        raise ValueError("tags must be a collection, not a string")
    result = frozenset(values)
    for tag in result:
        _text(tag, "tag")
    return result


@dataclass(frozen=True)
class Stats:
    """Observed panel values only; percentage fields use 0..100, not 0..1."""

    speed: Optional[float] = None
    attack: Optional[float] = None
    health: Optional[float] = None
    defense: Optional[float] = None
    crit_chance: Optional[float] = None
    effect_hit: Optional[float] = None
    effect_resist: Optional[float] = None

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if value is not None:
                _number(value, item.name, minimum=0)


@dataclass(frozen=True)
class Candidate:
    """ID is unique within one draft, including the owner (e.g. A:r2:s1)."""

    candidate_id: str
    owner: str
    name: str
    roles: FrozenSet[str] = field(default_factory=frozenset)
    soul_tags: FrozenSet[str] = field(default_factory=frozenset)
    stats: Stats = field(default_factory=Stats)
    confidence: float = 0.25
    strength: Optional[float] = None
    strength_source: str = ""

    def __post_init__(self):
        for label in ("candidate_id", "owner", "name"):
            _text(getattr(self, label), label)
        _number(self.confidence, "confidence", 0, 1)
        if self.strength is not None:
            _number(self.strength, "strength", 0, 100)
            _text(self.strength_source, "strength_source")
        if not isinstance(self.stats, Stats):
            raise ValueError("stats must be Stats")
        object.__setattr__(self, "roles", _tags(self.roles))
        object.__setattr__(self, "soul_tags", _tags(self.soul_tags))


@dataclass(frozen=True)
class RoleProfile:
    roles: FrozenSet[str]
    confidence: float
    source: str

    def __post_init__(self):
        object.__setattr__(self, "roles", _tags(self.roles))
        _number(self.confidence, "profile confidence", 0, 1)
        _text(self.source, "profile source")


@dataclass(frozen=True)
class RoleCatalog:
    version: str
    profiles: Mapping[str, RoleProfile]
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported catalogue schema_version")
        _text(self.version, "catalogue version")
        for name, profile in self.profiles.items():
            _text(name, "profile name")
            if not isinstance(profile, RoleProfile):
                raise ValueError("profiles must contain RoleProfile values")

    @classmethod
    def from_json(cls, path):
        """Load explicit reviewed metadata; do not infer tags from names."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("catalogue must be an object")
        if data.get("schema_version") != 1 or type(data.get("schema_version")) is not int:
            raise ValueError("unsupported catalogue schema_version")
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            raise ValueError("catalogue profiles must be an object")
        try:
            return cls(data["version"], {
                name: RoleProfile(_tags(profile["roles"]), profile["confidence"], profile["source"])
                for name, profile in profiles.items()
            })
        except (KeyError, TypeError) as exc:
            raise ValueError("incomplete role catalogue") from exc

    def annotate(self, candidate):
        profile = self.profiles.get(candidate.name)
        if profile is None:
            # Explicit observed tags are usable even without a name catalogue.
            if candidate.roles:
                return candidate
            return replace(candidate, confidence=min(candidate.confidence, 0.25))
        return replace(candidate, roles=candidate.roles | profile.roles,
                       confidence=min(candidate.confidence, profile.confidence))


@dataclass(frozen=True)
class StatWeight:
    name: str
    scale: float
    weight: float

    def __post_init__(self):
        if self.name not in {item.name for item in fields(Stats)}:
            raise ValueError("unknown stat: {}".format(self.name))
        _number(self.scale, "stat scale", minimum=0)
        if self.scale == 0:
            raise ValueError("stat scale must be positive")
        _number(self.weight, "stat weight")


@dataclass(frozen=True)
class Synergy:
    first_tag: str
    second_tag: str
    weight: float
    reason: str

    def __post_init__(self):
        for label in ("first_tag", "second_tag", "reason"):
            _text(getattr(self, label), label)
        _number(self.weight, "synergy weight")


@dataclass(frozen=True)
class Matchup:
    opponent_tag: str
    friendly_tag: str
    weight: float
    reason: str

    def __post_init__(self):
        for label in ("opponent_tag", "friendly_tag", "reason"):
            _text(getattr(self, label), label)
        _number(self.weight, "matchup weight")


@dataclass(frozen=True)
class GroupRule:
    """Every group needs a distinct observed member; alternatives are tags."""
    key: str
    groups: Tuple[FrozenSet[str], ...]
    weight: float
    reason: str

    def __post_init__(self):
        _text(self.key, "group rule key")
        _text(self.reason, "group rule reason")
        _number(self.weight, "group weight")
        if len(self.groups) < 2 or any(not g for g in self.groups):
            raise ValueError("Group rule requires at least two nonempty groups")


@dataclass(frozen=True)
class StrategyPolicy:
    """Tunable heuristic weights, not a simulation or learned tier list."""

    base_weight: float = 1.0
    uncertainty_penalty: float = 1.0
    low_confidence: float = 0.6
    missing_fire_penalty: float = 4.0
    heavy_fire_penalty: float = 2.0
    coverage: Tuple[Tuple[str, float], ...] = (
        ("damage", 3.0), ("fire", 3.0), ("heal", 1.0), ("shield", 1.0),
        ("control", 1.0), ("dispel", 1.0), ("pull", 1.0),
    )
    stat_weights: Tuple[StatWeight, ...] = (
        StatWeight("speed", 100.0, 1.0), StatWeight("attack", 1000.0, 0.1),
        StatWeight("health", 10000.0, 0.1),
    )
    synergies: Tuple[Synergy, ...] = (
        Synergy("fire", "heavy_fire", 1.0, "供火与重耗火职责互补（启发式）"),
        Synergy("pull", "damage", 0.5, "拉条与输出职责互补（启发式）"),
    )
    matchups: Tuple[Matchup, ...] = ()
    groups: Tuple[GroupRule, ...] = ()
    redundant_fire_penalty: float = 0.0

    def __post_init__(self):
        for label in ("base_weight", "uncertainty_penalty", "missing_fire_penalty",
                      "heavy_fire_penalty"):
            _number(getattr(self, label), label, minimum=0)
        _number(self.low_confidence, "low_confidence", 0, 1)
        role_names = []
        for role, weight in self.coverage:
            _text(role, "coverage role")
            _number(weight, "coverage weight", minimum=0)
            role_names.append(role)
        if len(role_names) != len(set(role_names)):
            raise ValueError("duplicate coverage role")
        if len({item.name for item in self.stat_weights}) != len(self.stat_weights):
            raise ValueError("duplicate stat weight")


@dataclass(frozen=True)
class ScoreTerm:
    key: str
    value: float
    reason: str


@dataclass(frozen=True)
class Evaluation:
    total: float
    terms: Tuple[ScoreTerm, ...]
    warnings: Tuple[str, ...]


@dataclass(frozen=True)
class RankedPlan:
    choices: Tuple[Candidate, ...]
    evaluation: Evaluation


def _unique(candidates):
    result = tuple(candidates)
    if any(not isinstance(item, Candidate) for item in result):
        raise ValueError("expected Candidate values")
    ids = [item.candidate_id for item in result]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate candidate_id")
    return tuple(sorted(result, key=lambda item: item.candidate_id))


def _owners(owners):
    if isinstance(owners, str):
        raise ValueError("owners must be a collection, not a string")
    result = tuple(owners)
    if len(result) != 2:
        raise ValueError("exactly two owners are required")
    for owner in result:
        _text(owner, "owner")
    if result[0] == result[1]:
        raise ValueError("owners must be distinct")
    return tuple(sorted(result))


def _validate_owners(candidates, owners):
    if any(item.owner not in owners for item in candidates):
        raise ValueError("candidate belongs to an unexpected owner")


def score_team(candidates: Iterable[Candidate], *, policy=StrategyPolicy(),
               opponent_tags: Iterable[str] = ()) -> Evaluation:
    """Evaluate observed candidates only. Missing measurements remain unknown."""
    team = _unique(candidates)
    observed_opponent = _tags(opponent_tags)
    terms = []
    warnings = []

    def add(key, value, reason):
        terms.append(ScoreTerm(key, value, reason))

    for candidate in team:
        cid = candidate.candidate_id
        confidence = candidate.confidence
        if candidate.strength is not None:
            add("strength:" + cid, candidate.strength / 10 * confidence,
                "{}: 资料强度 {} / 10（{}），非胜率".format(
                    cid, candidate.strength, candidate.strength_source))
        else:
            warnings.append("{}: 资料强度未知，未填写虚构分数".format(cid))
        add("base:" + cid, policy.base_weight * confidence,
            "{}: 基础分按识别可信度 {:.2f} 加权".format(cid, confidence))
        add("uncertainty:" + cid, -policy.uncertainty_penalty * (1 - confidence),
            "{}: 识别不确定性扣分".format(cid))
        if confidence < policy.low_confidence:
            warnings.append("{}: 低置信候选，仅可作保底，需复核".format(cid))
        if not candidate.roles:
            warnings.append("{}: 角色职责未知，未推断式神强度".format(cid))
        for rule in policy.stat_weights:
            value = getattr(candidate.stats, rule.name)
            if value is None:
                warnings.append("{}: {} 未识别，未按 0 计分".format(cid, rule.name))
                continue
            add("stat:{}:{}".format(cid, rule.name),
                confidence * value / rule.scale * rule.weight,
                "{}: 实测 {}={}，策略权重 {} / {}".format(
                    cid, rule.name, value, rule.weight, rule.scale))

    def tag_confidence(tag, roles_only=False):
        return max((item.confidence for item in team
                    if tag in (item.roles if roles_only else item.roles | item.soul_tags)),
                   default=0.0)

    for role, weight in policy.coverage:
        confidence = tag_confidence(role, roles_only=True)
        if confidence:
            add("coverage:" + role, weight * confidence, "职责覆盖: " + role)
    if team:
        fire = tag_confidence("fire", roles_only=True)
        add("missing_fire", -policy.missing_fire_penalty * (1 - fire),
            "供火标签覆盖不足惩罚；未知标签不等于确定不能供火")
        heavy = sum(item.confidence for item in team if "heavy_fire" in item.roles)
        add("heavy_fire", -policy.heavy_fire_penalty * max(0.0, heavy - 1) * (1 - fire),
            "重耗火标签重叠且缺少可靠供火时扣分；不模拟实际鬼火数量")
    for index, rule in enumerate(policy.synergies):
        confidence = max((min(first.confidence, second.confidence)
                          for first in team for second in team
                          if first.candidate_id != second.candidate_id
                          and rule.first_tag in first.roles | first.soul_tags
                          and rule.second_tag in second.roles | second.soul_tags), default=0.0)
        if confidence:
            add("synergy:{}".format(index), rule.weight * confidence, rule.reason)
    for index, rule in enumerate(policy.matchups):
        if rule.opponent_tag in observed_opponent:
            confidence = tag_confidence(rule.friendly_tag)
            if confidence:
                add("matchup:{}".format(index), rule.weight * confidence,
                    "已观察对手 {}: {}".format(rule.opponent_tag, rule.reason))
    for rule in policy.groups:
        def match(group_index, used, confidence):
            if group_index == len(rule.groups):
                return confidence
            return max((match(group_index+1, used | {c.candidate_id},
                              min(confidence, c.confidence))
                        for c in team if c.candidate_id not in used and
                        rule.groups[group_index] & (c.roles | c.soul_tags)), default=0.0)
        confidence = match(0, set(), 1.0)
        if confidence:
            add("group:"+rule.key, rule.weight*confidence, rule.reason)
    if policy.redundant_fire_penalty:
        excess = max(0, sum(c.confidence for c in team if "fire" in c.roles)-2)
        add("redundant_fire", -policy.redundant_fire_penalty*excess,
            "第三个及以上供火职责重复；双供火不一刀切惩罚")
    return Evaluation(sum(term.value for term in terms), tuple(terms), tuple(warnings))


def _rank(plans):
    return tuple(sorted(plans, key=lambda plan: (
        -plan.evaluation.total, tuple(item.candidate_id for item in plan.choices))))


def rank_draft_choices(candidates: Sequence[Candidate], selected: Sequence[Candidate],
                       owners: Sequence[str], *, active_owners=None,
                       policy=StrategyPolicy(), opponent_tags=()) -> Tuple[RankedPlan, ...]:
    """Rank picking ONE currently visible candidate per active owner.

    ``selected`` is both owners' confirmed draft so far. There is no future pool
    or future-round prediction. If an owner already confirmed this round, pass
    only the other owner in active_owners. Empty options produce no legal plans.
    """
    owners = _owners(owners)
    visible, confirmed = _unique(candidates), _unique(selected)
    _unique(visible + confirmed)
    _validate_owners(visible + confirmed, owners)
    if isinstance(active_owners, str):
        raise ValueError("active_owners must be a collection, not a string")
    active = owners if active_owners is None else tuple(active_owners)
    if not active or len(active) != len(set(active)) or any(owner not in owners for owner in active):
        raise ValueError("active_owners must be a nonempty distinct subset of owners")
    observed = _tags(opponent_tags)
    options = [tuple(item for item in visible if item.owner == owner) for owner in sorted(active)]
    plans = []
    for choices in product(*options):
        choices = _unique(choices)
        plans.append(RankedPlan(choices, score_team(confirmed + choices, policy=policy,
                                                  opponent_tags=observed)))
    return _rank(plans)


def rank_lineups(candidates: Sequence[Candidate], owners: Sequence[str], *, locked_ids=(),
                 per_owner=3, policy=StrategyPolicy(), opponent_tags=()) -> Tuple[RankedPlan, ...]:
    """Enumerate every legal lineup, with exactly per_owner from EACH owner.

    Locked IDs must be in the candidate pool and cannot be discarded. Invalid
    locks raise ValueError; an insufficient pool returns no legal plans.
    """
    owners = _owners(owners)
    team = _unique(candidates)
    _validate_owners(team, owners)
    if type(per_owner) is not int or per_owner < 1:
        raise ValueError("per_owner must be a positive integer")
    if isinstance(locked_ids, str):
        raise ValueError("locked_ids must be a collection, not a string")
    locked = tuple(locked_ids)
    if len(locked) != len(set(locked)):
        raise ValueError("duplicate locked_id")
    if not set(locked).issubset({item.candidate_id for item in team}):
        raise ValueError("locked_id is absent from the candidate pool")
    options = []
    for owner in owners:
        fixed = tuple(item for item in team if item.owner == owner and item.candidate_id in locked)
        if len(fixed) > per_owner:
            raise ValueError("too many locked candidates for owner " + owner)
        available = tuple(item for item in team if item.owner == owner and item.candidate_id not in locked)
        options.append(tuple(_unique(fixed + extra)
                             for extra in combinations(available, per_owner - len(fixed))))
    observed = _tags(opponent_tags)
    plans = []
    for pair in product(*options):
        choices = _unique(pair[0] + pair[1])
        plans.append(RankedPlan(choices, score_team(choices, policy=policy, opponent_tags=observed)))
    return _rank(plans)
