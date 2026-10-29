import os
from unittest.mock import Mock

import pytest

from deploy.adb import AdbManager
from deploy.config import ExecutionError
from deploy import official_adb


@pytest.mark.skipif(os.name != 'nt', reason='Windows managed ADB installer')
@pytest.mark.parametrize('replace', [False, True])
def test_adb_guard_uses_controlled_error(tmp_path, monkeypatch, replace):
    manager = object.__new__(AdbManager)
    manager.root_filepath = str(tmp_path)
    manager.__dict__['adb'] = str(tmp_path / 'toolkit/Lib/site-packages/adbutils/binaries/adb.exe')
    manager.ReplaceAdb = replace
    cause = official_adb.OfficialAdbError('ADB is running')
    install = Mock(side_effect=cause)
    emulator = Mock()
    monkeypatch.setattr(official_adb, 'ensure_official_adb', install)
    monkeypatch.setattr('deploy.adb.EmulatorConnect', emulator)
    with pytest.raises(ExecutionError) as failure:
        manager.adb_install()
    emulator.assert_not_called()
    if replace:
        install.assert_not_called()
    else:
        assert failure.value.__cause__ is cause
