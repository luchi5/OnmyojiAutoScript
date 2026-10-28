"""Recognize the redesigned incoming wanted invitation before legacy handling."""
from datetime import datetime
from functools import lru_cache

from module.atom.image import RuleImage
from module.base.timer import Timer
from module.exception import GameStuckError
from tasks.GlobalGame.config_emergency import FriendInvitation


@lru_cache(maxsize=1)
def rules():
    def rule(name, front, back, threshold=.85):
        return RuleImage(roi_front=front, roi_back=back, threshold=threshold,
                         method='Template matching', file=f'./tasks/GlobalGame/wanted_invitation/{name}.png')
    return {
        'accept': rule('accept_icon', (848,475,48,47), (825,455,100,95), .8),
        'reject': rule('reject', (934,573,53,29), (920,560,85,60)),
        'close': rule('close', (785,79,31,31), (770,65,65,65)),
        'stamina': rule('stamina', (677,517,44,47), (540,495,210,90)),
        # Reuse the verified red magatama icon, not the orange sushi cluster.
        'jade': RuleImage(roi_front=(677,517,43,46), roi_back=(530,490,230,100),
                          threshold=.85, method='Template matching',
                          file='./tasks/GlobalGame/gg/gg_g_jade.png'),
    }


def is_new_invitation(image):
    # Match the legacy flow: the accept icon alone identifies an invitation.
    return rules()['accept'].match(image)


def choose_action(task, image):
    policy = task.config.global_game.emergency.friend_invitation
    if policy == FriendInvitation.ACCEPT:
        return 'accept'
    if policy == FriendInvitation.REJECT:
        return 'reject'
    if policy == FriendInvitation.IGNORE:
        return 'close'
    if policy in (FriendInvitation.ONLY_JADE, FriendInvitation.JADE_AND_FOOD):
        if rules()['stamina'].match(image):
            return 'close'
        if rules()['jade'].match(image):
            return 'accept'
        if policy == FriendInvitation.JADE_AND_FOOD:
            for source in (task.I_G_CAT_FOOD, task.I_G_DOG_FOOD):
                # Keep the legacy food templates and search only the new reward row.
                rule = RuleImage(roi_front=tuple(source.roi_front), roi_back=(530,490,230,100),
                                 threshold=source.threshold, method=source.method, file=source.file)
                if rule.match(image):
                    return 'accept'
        return 'close'  # Unknown reward types never count as jade/food.
    raise GameStuckError(f'Unsupported wanted invitation policy: {policy}')


def handle_new_invitation(task):
    if not is_new_invitation(task.device.image):
        return None
    action = choose_action(task, task.device.image)
    button = rules()[action]
    record = task.device.detect_record
    timer = Timer(8).start()
    try:
        while not timer.reached():
            if not is_new_invitation(task.device.image):
                if action == 'accept':
                    task.set_next_run(task='WantedQuests', target=datetime.now().replace(microsecond=0))
                return True
            if button.match(task.device.image):
                task.click(button, interval=.8)
            task.device.screenshot()
    finally:
        task.device.detect_record = record
    raise GameStuckError('New wanted invitation did not disappear')
