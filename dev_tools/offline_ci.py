"""Run only reviewed offline suites, each in a fresh process. Never discover tests.

No production entrypoint is launched. The allowlist documents temporary-file,
AST and fake-process coverage; unknown tests require a separate safety review.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys

# Older public config fixtures predate their own logger stub. This runs again
# in multiprocessing spawn children, before importing any config model. It
# prevents production logger initialization (including log cleanup) in CI.
if os.environ.get('OAS_OFFLINE_STUB_LOGGER') == '1':
    import types
    from unittest.mock import Mock
    fake_logger = types.ModuleType('module.logger')
    fake_logger.logger = Mock()
    sys.modules['module.logger'] = fake_logger


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='Print selected suites without executing them')
    parser.add_argument('--report', type=Path, help='Optional JSON result path')
    parser.add_argument('--suite', help=argparse.SUPPRESS)
    args = parser.parse_args()
    plan = json.loads((ROOT / 'dev_tools' / 'offline_ci_suites.json').read_text(encoding='utf-8'))
    if args.suite:
        if args.suite not in {entry['path'] for entry in plan['suites']} or not args.suite.endswith('.py'):
            raise ValueError('Suite is not in the reviewed Python allowlist')
        if plan.get('stub_project_logger') is True and os.environ.get('OAS_OFFLINE_STUB_LOGGER') != '1':
            import types
            from unittest.mock import Mock
            os.environ['OAS_OFFLINE_STUB_LOGGER'] = '1'
            fake_logger = types.ModuleType('module.logger')
            fake_logger.logger = Mock()
            sys.modules['module.logger'] = fake_logger
        import unittest
        test = unittest.main(module=args.suite[:-3].replace('/', '.'), argv=[args.suite], exit=False)
        return 0 if test.result.wasSuccessful() else 1
    results = []
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    environment['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'tests'), environment.get('PYTHONPATH', '')])
    if plan.get('stub_project_logger') is True:
        environment['OAS_OFFLINE_STUB_LOGGER'] = '1'
    for suite in plan['suites']:
        relative = suite['path']
        if not re.fullmatch(r'(?:tests|dev_tools)/test_[a-z0-9_]+\.(?:py|js)', relative):
            raise ValueError('Invalid test allowlist entry')
        path = ROOT / relative
        if not path.is_file():
            status = 'missing_required' if suite.get('required') else 'not_present'
            print(f'[{status}] {relative}: suite is not included in this checkout', flush=True)
            results.append({'suite': relative, 'status': status})
            continue
        print(f'[offline] {relative}: {suite["scope"]}', flush=True)
        if suite.get('known_baseline'):
            print('Known baseline (still fails the gate if reproduced): ' + suite['known_baseline'], flush=True)
        if args.list:
            continue
        if path.suffix == '.py':
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=relative)
            command = [sys.executable, '-X', 'utf8', '-B', '-W', 'ignore::DeprecationWarning',
                       str(Path(__file__).resolve()), '--suite', relative]
        else:
            command = ['node', relative]
        try:
            completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                                       text=True, encoding='utf-8', errors='replace', timeout=180)
            output = completed.stdout + completed.stderr
            passed = completed.returncode == 0
            counts = re.findall(r'Ran (\d+) tests?', output)
            count = int(counts[-1]) if counts else None
            summaries = re.findall(r'^(?:OK(?: \([^\n]+\))?|FAILED \([^\n]+\))\s*$', output, re.MULTILINE)
            details = dict(re.findall(r'(failures|errors|skipped|expected failures|unexpected successes)=(\d+)', summaries[-1] if summaries else ''))
            metrics = {key.replace(' ', '_'): int(details.get(key, 0)) for key in
                       ('failures', 'errors', 'skipped', 'expected failures', 'unexpected successes')}
            # A failing test can also error in tearDown, so failures+errors
            # cannot reliably be subtracted to count passing tests on failure.
            metrics['passed_tests'] = count - metrics['skipped'] - metrics['expected_failures'] if count is not None and passed else None
            print(f'  {"PASS" if passed else "FAIL"}' + (f' ({count} tests; {metrics["skipped"]} skipped)' if count is not None else ''), flush=True)
            if not passed:
                print(output, flush=True)
            results.append({'suite': relative, 'status': 'passed' if passed else 'failed',
                            'returncode': completed.returncode, 'tests': count, **metrics})
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f'  FAIL: {type(error).__name__}', flush=True)
            results.append({'suite': relative, 'status': 'failed', 'error': type(error).__name__})
    failed = any(result['status'] in ('failed', 'missing_required') for result in results)
    report = {'project': plan['project'], 'listed_only': args.list,
              'passed': not failed, 'suites': results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('OFFLINE CHECKS ' + ('FAILED' if failed else 'LISTED' if args.list else 'PASSED'), flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
