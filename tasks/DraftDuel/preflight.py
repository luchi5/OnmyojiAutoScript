"""Read-only pair validation and deterministic admission checks.

This module never creates a Config/Device, starts a process, or saves an account.
Runtime activity and fee observations must be supplied by the caller.
"""
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
import re
from typing import Callable, Mapping

from pydantic import ValidationError

from tasks.DraftDuel.config import DraftDuelConfig, TeamRole


class PreflightError(ValueError):
    def __init__(self, code: str, reason: str):
        self.code = code
        super().__init__(reason)


@dataclass(frozen=True)
class AccountConfig:
    name: str
    serial: str
    emulator_name: str
    settings: DraftDuelConfig


@dataclass(frozen=True)
class PairConfig:
    accounts: tuple[AccountConfig, AccountConfig]

    @property
    def leader(self) -> str:
        return self.accounts[0].name

    @property
    def member(self) -> str:
        return self.accounts[1].name

    def for_account(self, name: str) -> AccountConfig:
        for account in self.accounts:
            if account.name == name:
                return account
        raise KeyError(name)


@dataclass(frozen=True)
class AccountActivity:
    running: bool
    task: str | None = None


@dataclass(frozen=True)
class AdmissionOffer:
    cost: int | None
    confidence: float
    ready: bool


@dataclass(frozen=True)
class PreflightResult:
    allowed: bool
    code: str
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


_UNKNOWN = frozenset(('', 'auto', 'unknown', 'none', 'null', '?'))
_WINDOWS_RESERVED = frozenset(('con', 'prn', 'aux', 'nul',
                               *(f'com{i}' for i in range(1, 10)),
                               *(f'lpt{i}' for i in range(1, 10))))


def _account_name(value: str) -> str:
    if not isinstance(value, str):
        raise PreflightError('invalid_account_name', 'Account name must be a string')
    name = value.strip()
    if (not name or name in ('.', '..') or name.endswith('.')
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.split('.')[0].casefold() in _WINDOWS_RESERVED):
        raise PreflightError('invalid_account_name', 'Account name must be a plain configuration name')
    return name


def _required_device_value(device: dict, field: str) -> str:
    value = device.get(field)
    if not isinstance(value, str) or value.strip().casefold() in _UNKNOWN:
        raise PreflightError('unknown_device', f'Explicit {field} is required for concurrent accounts')
    return value.strip()


def _serial_key(serial: str) -> str:
    key = serial.casefold()
    for loopback in ('localhost:', '[::1]:'):
        if key.startswith(loopback):
            return '127.0.0.1:' + key[len(loopback):]
    return key


def _load_account(root: Path, name: str) -> AccountConfig:
    # resolve also prevents an otherwise plain filename escaping through a link.
    source = (root / f'{name}.json').resolve()
    if source.parent != root:
        raise PreflightError('invalid_account_path', 'Account file must stay inside config_dir')
    if not source.is_file():
        raise PreflightError('account_missing', f'Account configuration does not exist: {name}')
    try:
        raw = json.loads(source.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValueError) as error:
        raise PreflightError('invalid_account_json', f'Cannot read account configuration: {name}') from error
    if not isinstance(raw, dict):
        raise PreflightError('invalid_account_json', 'Account configuration must be a JSON object')
    try:
        device = raw['script']['device']
        runtime = raw.get('draft_duel', {}).get('draft_duel_config', {})
        if not isinstance(device, dict) or not isinstance(runtime, dict):
            raise TypeError('Configuration groups must be objects')
        # Assignment validation rejects invalid limits rather than ConfigBase's
        # constructor fallback silently replacing an out-of-range stored value.
        settings = DraftDuelConfig()
        for key, value in runtime.items():
            if key in DraftDuelConfig.model_fields:
                setattr(settings, key, value)
    except (KeyError, AttributeError, TypeError, ValueError, ValidationError) as error:
        raise PreflightError('invalid_account_config', f'Invalid Draft Duel/device configuration: {name}') from error
    return AccountConfig(name, _required_device_value(device, 'serial'),
                         _required_device_value(device, 'emulatorinfo_name'), settings)


def load_pair_config(config_dir: str | Path, account_name: str) -> PairConfig:
    """Read both files, validate reciprocal pairing and return leader first.

    Scheduler enable flags are not changed or required here: this function also
    supports a manually requested one-shot run of a normally disabled task.
    """
    root = Path(config_dir).resolve()
    name = _account_name(account_name)
    first = _load_account(root, name)
    partner = _account_name(first.settings.partner_config)
    if partner.casefold() == name.casefold():
        raise PreflightError('same_account', 'A Draft Duel pair requires two different accounts')
    second = _load_account(root, partner)
    if second.settings.partner_config != first.name:
        raise PreflightError('nonreciprocal_pair', 'Both accounts must name each other as partner_config')
    if _serial_key(first.serial) == _serial_key(second.serial):
        raise PreflightError('same_device', 'Both accounts refer to the same device serial')
    if first.emulator_name.casefold() == second.emulator_name.casefold():
        raise PreflightError('same_emulator', 'Both accounts refer to the same emulator instance')
    roles = {first.settings.user_status, second.settings.user_status}
    if roles != {TeamRole.LEADER, TeamRole.MEMBER}:
        raise PreflightError('invalid_team_roles', 'Choose exactly one leader and one member')
    if not first.settings.teammate_name or not second.settings.teammate_name:
        raise PreflightError('missing_teammate_name', 'Both accounts must name their in-game teammate')
    return PairConfig(tuple(sorted((first, second),
                                  key=lambda account: account.settings.user_status != TeamRole.LEADER)))


def check_pair_activity(
        pair: PairConfig,
        activity_reader: Callable[[str], AccountActivity | None],
) -> PreflightResult:
    """Allow idle or DraftDuel workers; never stop or replace a busy worker."""
    for account in pair.accounts:
        try:
            activity = activity_reader(account.name)
        except Exception:
            return PreflightResult(False, 'activity_unknown', f'Cannot confirm activity for {account.name}')
        if not isinstance(activity, AccountActivity) or type(activity.running) is not bool:
            return PreflightResult(False, 'activity_unknown', f'Cannot confirm activity for {account.name}')
        if activity.running and activity.task != 'DraftDuel':
            return PreflightResult(False, 'account_busy', f'Account is already running another or unknown task: {account.name}')
    return PreflightResult(True, 'ready', 'Both accounts are available for Draft Duel')


def check_pair_admission(
        pair: PairConfig,
        offers: Mapping[str, AdmissionOffer],
        spent: Mapping[str, int],
        *,
        min_confidence: float = 0.90,
) -> PreflightResult:
    """Check both current offers against separate, caller-maintained budgets.

    Passing this check does not spend or reserve money. The coordinator must
    serialize admission, confirm the same phase/offer before clicking, and
    persist each account's verified expenditure before admitting another run.
    """
    if (isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float))
            or not isfinite(min_confidence) or not 0 <= min_confidence <= 1):
        raise ValueError('min_confidence must be between zero and one')
    for account in pair.accounts:
        offer = offers.get(account.name)
        if not isinstance(offer, AdmissionOffer) or offer.ready is not True:
            return PreflightResult(False, 'account_not_ready', f'Account has not confirmed readiness: {account.name}')
        if type(offer.cost) is not int or offer.cost < 0:
            return PreflightResult(False, 'cost_unknown', f'Admission fee is not confirmed: {account.name}')
        if (isinstance(offer.confidence, bool) or not isinstance(offer.confidence, (int, float))
                or not isfinite(offer.confidence)
                or not min_confidence <= offer.confidence <= 1):
            return PreflightResult(False, 'cost_low_confidence', f'Admission fee confidence is too low: {account.name}')
        used = spent.get(account.name)
        if type(used) is not int or used < 0:
            return PreflightResult(False, 'budget_unknown', f'Expenditure is not confirmed: {account.name}')
        if offer.cost > 0:
            if account.settings.free_only:
                return PreflightResult(False, 'free_only', f'Account allows free admission only: {account.name}')
            if offer.cost > account.settings.coin_budget - used:
                return PreflightResult(False, 'budget_exceeded', f'Admission exceeds the remaining budget: {account.name}')
    return PreflightResult(True, 'ready', 'Both accounts are ready and both fees fit their budgets')
