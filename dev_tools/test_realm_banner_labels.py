"""Replay animated realm plaques; verify coordinates and reject unrelated pages."""
import unittest
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
from tasks.Component.Costume.costume_base import CostumeBase

class RealmLabelsTest(unittest.TestCase):
 def test_replay(self):
  for fixture in ['realm_banner15_current.png','realm_banner15_error.png','realm_banner14_screen.png','realm_banner14_current.png','realm_banner9_compact.png']:
   image=cv2.imread(str(Path('dev_tools/fixtures')/fixture))[:,:,::-1]
   h=SimpleNamespace()
   from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets
   from tasks.Component.Costume.costume_base import _clone_image
   for key in ['GROWN','CARD','DEFENSE']:setattr(h,'I_SHI_'+key,_clone_image(getattr(KekkaiUtilizeAssets,'I_SHI_'+key)))
   CostumeBase._add_realm_label_variants(h)
   coords = [('GROWN',616),('CARD',794),('DEFENSE',430)] if '14_' in fixture else [('GROWN',622),('CARD',831),('DEFENSE',414)]
   if '9_compact' in fixture: coords = [('GROWN',634),('CARD',768),('DEFENSE',499)]
   for key,x in coords:
    rule=getattr(h,'I_SHI_'+key)
    self.assertTrue(rule.match(image), (fixture,key))
    self.assertLess(abs(rule.roi_front[0]-x),4)
    self.assertFalse(rule.match(np.zeros_like(image)))
   before=len(h.I_SHI_GROWN.targets)
   CostumeBase._add_realm_label_variants(h)
   self.assertEqual(len(h.I_SHI_GROWN.targets),before)
 def test_skin_selectors_are_not_boxes_or_flags(self):
  from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets as A
  from tasks.Component.Costume.costume_base import _clone_image
  h=SimpleNamespace()
  for key in ['I_BOX_EXP','I_BOX_EXP_MAX','I_SHI_GROWN','I_SHI_CARD','I_SHI_DEFENSE']:
   setattr(h,key,_clone_image(getattr(A,key)))
  old_roi=h.I_BOX_EXP.roi_back
  CostumeBase._add_realm_label_variants(h)
  self.assertEqual(old_roi,h.I_BOX_EXP.roi_back)
  for fixture in ['realm_skin_selector3.png','realm_skin_selector5.png']:
   image=cv2.imread(str(Path('dev_tools/fixtures')/fixture))[:,:,::-1]
   self.assertFalse(h.I_BOX_EXP.match(image,threshold=.6))
   for key in ['I_SHI_GROWN','I_SHI_CARD','I_SHI_DEFENSE']:
    self.assertFalse(getattr(h,key).match_multi_scale(image),(fixture,key))

if __name__=='__main__':unittest.main()
