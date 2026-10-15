"""拾此一瞬：从活动总览进入磐长故地，独立于体力副本。"""

from tasks.ActivityShikigami.current_event import CurrentEventAssets as E
from tasks.GameUi.page import Page, all_of, page_main, page_act_list
from tasks.GameUi.assets import GameUiAssets as G


page_climb_event_list = Page(all_of(G.I_CHECK_ACT_LIST, E.I_EVENT_LIST_ART))
page_act_list.connect(page_climb_event_list, E.I_EVENT_VERSION)
page_climb_event_list.connect(page_main, G.I_BACK_Y)

page_climb_act = Page(E.I_EVENT_MAIN)
page_climb_event_list.connect(page_climb_act, G.I_ACT_LIST_GOTO_ACT)
page_climb_act.connect(page_climb_event_list, G.I_BACK_Y)

page_climb_map = Page(E.I_EVENT_MAP)
page_climb_act.connect(page_climb_map, E.I_EVENT_TICKET_ENTRY)
page_climb_map.connect(page_climb_act, E.I_EVENT_MAP_BACK)

page_climb_ticket = Page(all_of(E.I_TICKET_PAGE, E.I_TICKET_CHALLENGE))
page_climb_map.connect(page_climb_ticket, E.I_EVENT_MAP_BATTLE)
page_climb_ticket.connect(page_climb_map, G.I_BACK_Y)
