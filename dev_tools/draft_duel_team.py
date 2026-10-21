"""01/09 invitation experiment. --execute permits team IO only.

Does not create Device, start/restart emulators, or enter a challenge.
Leader navigation uses the already verified page templates; member stays put.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
from tasks.DraftDuel.config import DraftDuelConfig, TeamRole
from tasks.DraftDuel.local_draft import AdbIO, TimedOCR
from tasks.DraftDuel.team import TeamVision, run_team


class TeamAdbIO(AdbIO):
    def tap_point(self, x, y):
        self.input('tap', x, y)

    def swipe(self, x1, y1, x2, y2, ms):
        self.input('swipe', x1, y1, x2, y2, ms)

    def input(self, *args):
        subprocess.run(self.command+['shell', 'input']+list(map(str, args)),
                       capture_output=True, check=True, timeout=3,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def navigate_leader(io, vision, own_name, event):
    """Only the captured 01 courtyard route; no fallback/guessed coordinates."""
    template = cv2.imread(str(ROOT/'tasks/GameUi/page/page_town_goto_draft_duel.png'))
    vision.templates['town_draft'] = template
    last_click = 0
    end = time.monotonic()+25
    while time.monotonic() < end:
        image, _ = io.capture()
        if vision.match(image, 'title', (140, 8, 140, 45)):
            return True
        if time.monotonic()-last_click < 2:
            time.sleep(.3)
            continue
        point = None
        if vision.text(image, (125, 26, 165, 34)) == own_name:
            point = vision.match(image, 'courtyard_town', (650, 140, 150, 100), .87)
        if point is None:
            # The town mode icon is unique and anchored to the observed strip.
            point = vision.match(image, 'town_draft', (170, 145, 567, 100), .85)
        if point:
            event({'view': 'leader_navigation', 'action': 'navigate', 'point': point}, image)
            io.tap_point(*point)
            last_click = time.monotonic()
        time.sleep(.3)
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--account', choices=('01', '09'), required=True)
    p.add_argument('--role', choices=('leader', 'member'), required=True)
    p.add_argument('--teammate', required=True)
    p.add_argument('--own-name', required=True)
    p.add_argument('--tab', choices=('好友', '最近', '寮友', '跨区'), default='跨区')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--navigate', action='store_true', help='leader only: captured courtyard/town route')
    p.add_argument('--seconds', type=int, default=120)
    p.add_argument('--folder', type=Path, required=True)
    args = p.parse_args()
    if not 1 <= args.seconds <= 600 or not args.teammate.strip():
        p.error('seconds must be 1..600 and teammate must be nonempty')
    args.folder.mkdir(parents=True, exist_ok=True)
    serial = {'01': '127.0.0.1:16384', '09': '127.0.0.1:16640'}[args.account]
    io = TeamAdbIO(ROOT/'toolkit/Lib/site-packages/adbutils/binaries/adb.exe', serial)
    settings = DraftDuelConfig(user_status=args.role, teammate_name=args.teammate, friend_tab=args.tab)
    records = []
    def event(record, image):
        record = dict(record, time=time.time())
        records.append(record)
        cv2.imwrite(str(args.folder/('%s-%03d-%s.png' % (args.account, len(records), record['action']))), image)
        (args.folder/(args.account+'-team.json')).write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(record, ensure_ascii=False), flush=True)
    ocr = TimedOCR()
    try:
        vision = TeamVision(ocr)
        if args.navigate and args.execute and args.role == 'leader':
            if not navigate_leader(io, vision, args.own_name, event):
                print('Cannot verify leader navigation; no invitation attempted', flush=True)
                return
        ok = run_team(io, vision, settings, args.own_name,
                      execute=args.execute, seconds=args.seconds, event=event)
        print(json.dumps({'team_ready': ok}, ensure_ascii=False), flush=True)
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
