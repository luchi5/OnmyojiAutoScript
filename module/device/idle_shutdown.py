"""Close an already-running configured MuMu instance without initializing Device."""
from pathlib import Path
import subprocess
from module.device.platform2.mumu_process import MuMuProcessGuard


def stop_idle_emulator(config, logger):
    device = config.script.device
    launcher = Path(device.emulatorinfo_path)
    if device.emulatorinfo_type != 'MuMuPlayer12' or launcher.name.lower() != 'mumunxmain.exe':
        logger.warning('Cold idle shutdown is not supported for this emulator; retain unknown state')
        return False
    try:
        guard = MuMuProcessGuard(device.emulatorinfo_name, str(launcher), logger)
        if not guard.processes():
            logger.info('Configured emulator is already stopped; no startup or shutdown needed')
            return True
        engine = '15' if '-15.0-' in guard.name else '12'
        logger.info(f'Close already-running idle emulator: {guard.name}')
        try:
            subprocess.run([str(launcher.with_name('MuMuManager.exe')), 'control', '-v',
                            guard.index, '--version', engine, 'shutdown'],
                           timeout=30, check=False, capture_output=True,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            logger.warning('Idle shutdown command timed out; verifying instance processes')
        guard.ensure_stopped()
        return not guard.processes()
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(f'Cannot verify idle emulator shutdown: {exc}')
        return False


def idle_emulator_running(config, logger):
    """Read-only probe; None means unknown, never permission to launch."""
    device = config.script.device
    launcher = Path(device.emulatorinfo_path)
    if device.emulatorinfo_type != 'MuMuPlayer12' or launcher.name.lower() != 'mumunxmain.exe':
        return None
    try:
        guard = MuMuProcessGuard(device.emulatorinfo_name, str(launcher), logger)
        return bool(guard.processes())
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(f'Cannot inspect idle emulator: {exc}')
        return None
