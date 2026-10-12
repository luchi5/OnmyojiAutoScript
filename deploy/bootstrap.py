"""Prepare a downloaded Windows source ZIP without a preinstalled OAS."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

from deploy.config import DeployConfig, ExecutionError
from deploy.official_adb import ensure_official_adb, _safe, VERSION
from deploy.pip import PipManager
from deploy.patch import check_running_directory, patch_uiautomator2
from deploy.utils import poor_yaml_read, poor_yaml_write

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / 'toolkit/OASX/oasx_luchi-2026.10.11/app/oasx.exe'


def seed_config(root=ROOT):
    """Create only a missing config; never migrate somebody's account settings."""
    target = root / 'config/deploy.yaml'
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    values = poor_yaml_read(str(root / 'deploy/template'))
    values.update(Repository='https://github.com/luchi5/OnmyojiAutoScript.git',
                  Branch='luchi', GitExecutable='./toolkit/Git/cmd/git.exe',
                  AdbExecutable='./toolkit/adb/adb.exe', ReplaceAdb=False,
                  InstallUiautomator2=False, WebuiHost='127.0.0.1',
                  WebuiPort=22289, Run=None, KeepLocalChanges=True)
    # poor_yaml_write uses the repository's template, including its comments.
    poor_yaml_write(values, str(target))
    return True


def connect_source(config, root=ROOT):
    """Give source ZIP users Git update metadata without replacing their files."""
    git = config.filepath('GitExecutable')
    if not Path(git).exists():
        alternatives = (root / 'toolkit/Git/cmd/git.exe', root / 'toolkit/Git/mingw64/bin/git.exe')
        git = str(next((path for path in alternatives if path.exists()), Path(git)))
    def run(*args):
        subprocess.run([git, '-c', 'http.sslBackend=openssl', *args], cwd=root, check=True)
    if (root / '.git').exists():
        probe = subprocess.run([git, 'rev-parse', '--verify', 'HEAD'], cwd=root,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            return
    print('为下载的源码配置后续更新仓库。', flush=True)
    run('init', '--initial-branch=' + config.Branch)
    remote = subprocess.run([git, 'remote', 'get-url', 'origin'], cwd=root,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if remote.returncode != 0:
        run('remote', 'add', 'origin', config.Repository)
    try:
        run('fetch', '--depth=1', 'origin', config.Branch)
        run('reset', '--mixed', 'FETCH_HEAD')
        run('branch', '--set-upstream-to=origin/' + config.Branch)
    except subprocess.CalledProcessError:
        # The source ZIP remains runnable. A later invocation retries the fetch.
        print('暂时无法连接 GitHub；保留下载的源码继续安装，可稍后在 OASX 更新。', flush=True)


def dependency_fingerprint(root=ROOT):
    digest = hashlib.sha256()
    digest.update((root / 'requirements.txt').read_bytes())
    digest.update(sys.version.encode())
    return digest.hexdigest()


def install_dependencies(config, root=ROOT):
    marker = root / 'toolkit/.oas-dependencies.json'
    fingerprint = dependency_fingerprint(root)
    try:
        cached = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cached = {}
    if cached.get('fingerprint') == fingerprint and requirements_installed(root):
        check = subprocess.run([sys.executable, '-m', 'pip', 'check'], cwd=root)
        if check.returncode == 0:
            print('依赖已经安装，跳过重复下载。', flush=True)
            return
    manager = PipManager()
    manager.pip_install()
    if not config.InstallDependencies:
        raise RuntimeError('首次安装需要开启 config/deploy.yaml 的 InstallDependencies。')
    subprocess.run([sys.executable, '-m', 'pip', 'check'], cwd=root, check=True)
    subprocess.run([sys.executable, '-c',
                    'import cv2,onnxruntime,win32api; from module.server.app import fastapi_app'],
                   cwd=root, check=True)
    marker.write_text(json.dumps({'fingerprint': fingerprint}), encoding='utf-8')


def requirements_installed(root=ROOT):
    for line in (root / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        requirement = line.split('#', 1)[0].strip()
        if not requirement:
            continue
        name, version = requirement.split('==', 1)
        try:
            if importlib.metadata.version(name.strip()) != version.strip():
                return False
        except importlib.metadata.PackageNotFoundError:
            return False
    return True


def port_is_busy(port):
    with socket.socket() as connection:
        connection.settimeout(1)
        return connection.connect_ex(('127.0.0.1', port)) == 0


def launch(config, no_frontend=False, root=ROOT):
    port = int(config.WebuiPort)
    if port_is_busy(port):
        raise RuntimeError(f'端口 {port} 已被占用；本启动器不会结束已有后台或任务。')
    process = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', 'server.py',
                                '--host', '127.0.0.1', '--port', str(port)], cwd=root)
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('OAS 后台启动失败，请查看上面的错误。')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/home/test', timeout=2) as response:
                    if json.load(response).get('message') == 'test':
                        break
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError('等待 OAS 后台启动超时。')
        if not no_frontend:
            # A fresh bundled client already defaults to 127.0.0.1:22289.
            if not CLIENT.exists():
                raise RuntimeError('OASX 客户端缺失，请重新运行安装入口。')
            subprocess.Popen([str(CLIENT)], cwd=CLIENT.parent)
        print(f'OAS 已启动：127.0.0.1:{port}。关闭此窗口会结束本次后台。', flush=True)
        if port != 22289 and not no_frontend:
            print(f'已有配置保留了端口 {port}，请在 OASX 设置中填写该地址。', flush=True)
        return process.wait()
    finally:
        if process.poll() is None:
            # Only the backend created by this invocation is ever stopped.
            process.terminate()
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--no-frontend', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    check_running_directory()
    seed_config()
    config = DeployConfig()
    if not args.prepare_only and port_is_busy(int(config.WebuiPort)):
        raise RuntimeError(f'端口 {config.WebuiPort} 已被占用，请使用已运行的后台。')
    connect_source(config)
    install_dependencies(config)
    patch_uiautomator2()
    print('准备官方 ADB（不连接或重启模拟器）。', flush=True)
    download = ROOT / 'log/installer/downloads' / ('platform-tools-' + VERSION + '.zip')
    if download.exists():
        cache = _safe(Path(config.filepath('AdbExecutable')).parent / '.official-adb')
        cache.mkdir(parents=True, exist_ok=True)
        cached_archive = _safe(cache / download.name, file=True)
        if not cached_archive.exists():
            shutil.copyfile(download, cached_archive)
    result = ensure_official_adb(config.filepath('AdbExecutable'))
    print('官方 ADB：' + result, flush=True)
    if args.prepare_only:
        print('安装完成。下次双击“安装并启动.cmd”即可打开 OAS。', flush=True)
        return 0
    return launch(config, args.no_frontend)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ExecutionError, subprocess.CalledProcessError) as error:
        print('安装或启动失败：' + str(error), file=sys.stderr, flush=True)
        sys.exit(1)
