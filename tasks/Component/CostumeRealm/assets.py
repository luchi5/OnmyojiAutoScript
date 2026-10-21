from module.atom.image import RuleImage

class CostumeRealmAssets:
    I_REALM_SHIN = RuleImage(roi_front=(371,490,45,55), roi_back=(340,455,500,135), threshold=0.8, method="Template matching", file="./tasks/Component/CostumeRealm/shen_an/i_realm_shin.png")
    I_SHI_DEFENSE = RuleImage(roi_front=(494,488,17,48), roi_back=(340,455,500,135), threshold=0.8, method="Template matching", file="./tasks/Component/CostumeRealm/shen_an/i_shi_defense.png")
    I_SHI_GROWN = RuleImage(roi_front=(632,488,17,48), roi_back=(340,455,500,135), threshold=0.8, method="Template matching", file="./tasks/Component/CostumeRealm/shen_an/i_shi_grown.png")
    I_SHI_CARD = RuleImage(roi_front=(765,488,17,48), roi_back=(340,455,500,135), threshold=0.8, method="Template matching", file="./tasks/Component/CostumeRealm/shen_an/i_shi_card.png")
    I_CARD_EXP = RuleImage(roi_front=(615,416,50,36), roi_back=(340,320,650,300), threshold=0.8, method="Template matching", file="./tasks/Component/CostumeRealm/shen_an/i_card_exp.png")
    I_BOX_EXP = RuleImage(roi_front=(746,418,48,38), roi_back=(340,320,650,300), threshold=0.8, method="Template matching", file="./tasks/Component/CostumeRealm/shen_an/i_box_exp.png")
    I_BOX_AP = RuleImage(roi_front=(824,519,36,33), roi_back=(340,320,650,300), threshold=0.8, method="Template matching", file="./tasks/Component/CostumeRealm/shen_an/i_box_ap.png")
