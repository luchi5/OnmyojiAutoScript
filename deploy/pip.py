# This Python file uses the following encoding: utf-8
# copy from alas https://github.com/LmeSzinc/AzurLaneAutoScript
from urllib.parse import urlparse
import subprocess

from deploy.config import DeployConfig, ExecutionError
from deploy.logger import logger
from deploy.utils import *


class PipManager(DeployConfig):
    def install_with_fallback(self, packages):
        mirrors = list(dict.fromkeys(filter(None, (
            self.PypiMirror, 'https://pypi.tuna.tsinghua.edu.cn/simple',
            'https://pypi.org/simple',
        ))))
        for mirror in mirrors:
            args = ['-i', mirror, '--disable-pip-version-check', '--timeout', '20', '--retries', '1']
            if mirror.startswith('http:') or not self.SSLVerify:
                args += ['--trusted-host', urlparse(mirror).hostname]
                if urlparse(mirror).hostname == 'pypi.org':
                    args += ['--trusted-host', 'files.pythonhosted.org']
            command = [self.python, '-m', 'pip', '--isolated', 'install', *packages, *args]
            logger.info(subprocess.list2cmdline(command))
            if subprocess.run(command).returncode == 0:
                return
            logger.warning('Dependency installation failed; trying the next package index.')
        raise ExecutionError('All package indexes failed. Check network/proxy and retry.')

    @cached_property
    def python(self):
        return self.filepath("PythonExecutable")

    @cached_property
    def requirements_file(self):
        if self.RequirementsFile == 'requirements.txt':
            return 'requirements.txt'
        else:
            return self.filepath("RequirementsFile")

    @cached_property
    def pip(self):
        return f'"{self.python}" -m pip'

    def pip_install(self):
        logger.hr('Update Dependencies', 0)

        if not self.InstallDependencies:
            logger.info('InstallDependencies is disabled, skip')
            return

        logger.hr('Check Python', 1)
        subprocess.run([self.python, '--version'], check=True)

        logger.hr('Update Dependencies', 1)
        # Embedded Python needs build tools in its own site-packages: its _pth
        # file deliberately ignores the temporary PYTHONPATH used by isolation.
        self.install_with_fallback(['setuptools==80.9.0', 'wheel==0.45.1'])
        self.install_with_fallback(['-r', self.requirements_file, '--no-build-isolation'])
