"""Bounded shutdown checks for one modern MuMu instance, without global kills."""
import ntpath
from pathlib import Path
import re
import time
from uuid import UUID
import xml.etree.ElementTree as ET

import psutil


class MuMuProcessGuard:
    def __init__(self, name, launcher, logger):
        match = re.fullmatch(r'MuMuPlayer(?:Global)?-(12|15)\.0-(\d+)', name)
        if not match:
            raise RuntimeError(f'Cannot safely identify MuMu instance: {name}')
        self.name, self.index = name, match.group(2)
        self.root = Path(launcher).parent.parent
        self.logger = logger
        # Read the VM identity, not just its display index. Never guess a UUID.
        config = self.root / 'vms' / name / f'{name}.nemu'
        machine = ET.parse(config).getroot().find('{*}Machine')
        if machine is None or machine.get('name') != name:
            raise RuntimeError(f'MuMu VM identity does not match: {name}')
        self.uuid = str(UUID(machine.attrib['uuid'].strip('{}')))

    @staticmethod
    def _option(args, key):
        positions = [i for i, value in enumerate(args) if value == key]
        if len(positions) != 1 or positions[0] + 1 >= len(args):
            return None
        return args[positions[0] + 1]

    def matches(self, process):
        name = process.name().lower()
        if name not in {'mumunxdevice.exe', 'mumuvmmheadless.exe'}:
            return False
        args = process.cmdline()
        if not args:
            raise RuntimeError(f'Cannot inspect MuMu process {process.pid}; stop recovery')
        if ntpath.basename(process.exe()).lower() != name:
            return False
        if name == 'mumunxdevice.exe':
            root = ntpath.normcase(ntpath.normpath(str(self.root))) + '\\'
            exe = ntpath.normcase(ntpath.normpath(process.exe()))
            if not exe.startswith(root):
                return False
            vm, index = self._option(args, '--vm'), self._option(args, '-v')
            if (self.name in args and (vm != self.name or index is None)
                    or vm is None and index == self.index):
                raise RuntimeError(f'Incomplete identity for MuMu process {process.pid}; stop recovery')
            if vm == self.name and index != self.index:
                raise RuntimeError(f'Conflicting identity for MuMu process {process.pid}; stop recovery')
            return vm == self.name and index == self.index
        uuid = self._option(args, '--startvm')
        comment = self._option(args, '--comment')
        if (self.name in args and (comment != self.name or not uuid)
                or comment is None and uuid is not None and uuid.strip('{}').lower() == self.uuid):
            raise RuntimeError(f'Incomplete identity for MuMu process {process.pid}; stop recovery')
        if comment == self.name:
            try:
                UUID(uuid.strip('{}'))
            except ValueError as error:
                raise RuntimeError(f'Invalid identity for MuMu process {process.pid}; stop recovery') from error
        return comment == self.name and uuid is not None and uuid.strip('{}').lower() == self.uuid

    def processes(self):
        result = []
        for process in psutil.process_iter():
            try:
                if self.matches(process):
                    result.append(process)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as error:
                # Unknown identity is not proof of exit or permission to kill.
                raise RuntimeError('Cannot verify MuMu processes; stop recovery') from error
        return result

    def wait_stopped(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if not self.processes():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def ensure_stopped(self, grace=20, kill_timeout=10):
        if self.wait_stopped(grace):
            return
        self.logger.warning(f'MuMu {self.name} did not exit; clearing only this instance')
        # Close the instance frontend first; shared managers, services and ADB
        # are never candidates. psutil also checks PID reuse before kill().
        targets = sorted(self.processes(), key=lambda process: process.name().lower())
        for process in targets:
            try:
                if self.matches(process):
                    self.logger.warning(f'Clear MuMu instance {self.name}, PID {process.pid}')
                    process.kill()
            except psutil.NoSuchProcess:
                pass
        if not self.wait_stopped(kill_timeout):
            raise RuntimeError(f'MuMu {self.name} still has processes; refusing another launch')
        self.logger.info(f'MuMu {self.name} processes have exited')
