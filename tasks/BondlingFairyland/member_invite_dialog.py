"""Handle the member auto-accept modal before the invitation behind it."""
from module.base.timer import Timer
from module.exception import GameStuckError
from tasks.BondlingFairyland.invite_dialog import rule


def handle_member_invite(task):
    title = rule('member_title', (494, 274, 300, 60), (480, 260, 328, 88))
    confirm = rule('confirm', (703, 420, 106, 38), (690, 408, 133, 62))
    if not task.appear(title):
        return False
    limit = Timer(6).start()
    while not limit.reached():
        task.screenshot()
        if not task.appear(title):
            return True
        task.appear_then_click(confirm, interval=1)
    raise GameStuckError('Bondling member auto-accept dialog did not close')
