from tasks.GameUi.page import Page, page_entertainment
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.RichMan.assets import RichManAssets

page_itachi_shop = Page(RichManAssets.I_ITACHI_SHOP_CHECK, key="page_itachi_shop", priority=75, category="rich_man")
page_entertainment.connect(page_itachi_shop, RichManAssets.I_ITACHI_SHOP_ENTRY, key="page_entertainment->page_itachi_shop")
page_itachi_shop.connect(page_entertainment, GlobalGameAssets.I_UI_BACK_RED, key="page_itachi_shop->page_entertainment")
