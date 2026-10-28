"""Build a shareable offline OCR companion bundle, with no local account data."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.ocr_installer import verify_wheels


def build(wheels, output):
    manifest = json.loads((ROOT / 'deploy/rapidocr-manifest.json').read_text('utf-8'))
    verify_wheels(wheels, manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents replacing an already distributed bundle.
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name in ('deploy/ocr_installer.py', 'deploy/rapidocr-manifest.json',
                     'deploy/requirements-rapidocr-lock.txt', 'deploy/install-rapidocr.cmd',
                     'deploy/RAPIDOCR_BUNDLE.md'):
            archive.write(ROOT / name, name)
        for entry in manifest['wheels']:
            archive.write(wheels / entry['name'], 'deploy/rapidocr-wheels/' + entry['name'])
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(checksum + '  ' + output.name + '\n', encoding='ascii')
    print(f'{output}\nSHA256: {checksum}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.wheels, args.output)
