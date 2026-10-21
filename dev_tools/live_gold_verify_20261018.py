import sys,json,traceback
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
import cv2
from copy import deepcopy
from module.config.config import Config
from module.device.device import Device
from tasks.Restart.login import LoginHandler
from tasks.GoldYoukai.script_task import ScriptTask
from tasks.GameUi.page import page_main
out=Path('log/live-gold-20261018');out.mkdir(exist_ok=True)
results=[]
def save(name):
 cv2.imwrite(str(out/(name+'.png')),cv2.cvtColor(d.image,cv2.COLOR_RGB2BGR))
def awake(name):
 t.open_buff();area=t.get_area_image(t.I_AWAKE)
 assert area, 'Awake row missing'
 a,b=deepcopy(t.I_OPEN_YELLOW),deepcopy(t.I_CLOSE_RED)
 a.roi_back=b.roi_back=area
 on,off=a.match(d.image),b.match(d.image)
 save(name)
 assert on!=off, 'Ambiguous Awake state'
 return bool(on)
c=Config('01-自己');c.model.running_task=''
d=Device(config=c)
try:
 if not d.app_is_running():
  h=LoginHandler(config=c,device=d);h._allow_login_restart=False
  d.app_start();h.app_handle_login()
 t=ScriptTask(config=c,device=d);t.goto_page(page_main)
 baseline=awake('baseline-awake')
 for pct in [50,100]:
  for desired in [True,False]:
   ok=getattr(t,'gold_'+str(pct))(desired)
   save(f'gold-{pct}-{desired}')
   unchanged=awake(f'awake-after-{pct}-{desired}')==baseline
   result=dict(percent=pct,desired=desired,verified=ok,awake_unchanged=unchanged)
   results.append(result);print('LIVE_RESULT',json.dumps(result),flush=True)
   assert ok and unchanged,result
 t.close_buff()
except Exception:
 traceback.print_exc()
finally:
 (out/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
