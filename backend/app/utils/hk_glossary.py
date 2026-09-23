# -*- coding: utf-8 -*-
"""Taiwan -> Hong Kong vocabulary pass for machine-translated Traditional Chinese.

Mined from 5,983 blocks where the same Japanese source was translated twice: once by
DeepSeek under the production Hong Kong prompt (the HK reference) and once by
qwen-mt-flash with target_lang=Traditional Chinese (the Taiwan-flavoured output).
Counts are occurrences in that corpus; entries marked "seed" were not mined but pair
with one that was.

Deliberately NOT included:
  * OpenCC t2hk. Measured against the HK reference corpus, DeepSeek writes 說/溫/戶/為,
    not the HK-standard 説/温/户/爲, so t2hk would move the text away from the house
    style, not toward it. The one character-variant difference that is real is 裡 -> 裏
    (qwen 219 : 5, DeepSeek 2 : 226), and it is in the table below.
  * Anything that changes sentence structure. This pass fixes words; it cannot fix
    Taiwanese phrasing.
  * The pairs in REJECTED - mined and real, but ruled out by Chin Ho on 2026-09-21.
"""
import re

# category, tw, hk, corpus_count, note
APPLY = [
    # --- technology -------------------------------------------------------
    ("Technology", "網路安全", "網絡保安", 5, ""),
    ("Technology", "網路", "網絡", 64, ""),
    ("Technology", "軟體", "軟件", 22, ""),
    ("Technology", "硬體", "硬件", 0, "seed: pairs with 軟體"),
    ("Technology", "數位", "數碼", 23, ""),
    ("Technology", "線上", "網上", 21, ""),
    ("Technology", "智慧型手機", "智能手機", 19, ""),
    ("Technology", "智慧手機", "智能手機", 0, "seed variant"),
    ("Technology", "部落格", "網誌", 6, ""),
    ("Technology", "社群媒體", "社交平台", 16, ""),
    ("Technology", "社群網站", "社交網站", 0, "seed variant"),
    ("Technology", "機器人", "機械人", 13, ""),
    ("Technology", "遠端", "遙距", 13, ""),
    ("Technology", "內建", "內置", 10, ""),
    ("Technology", "基礎設施", "基建", 10, ""),
    ("Technology", "駭客", "黑客", 5, "mined as 白帽駭客 -> 白帽黑客"),
    ("Technology", "上傳", "上載", 6, ""),
    ("Technology", "資訊流", "動態", 7, "social feed"),
    ("Technology", "液晶螢幕", "液晶屏幕", 4, ""),
    ("Technology", "解析度", "解像度", 0, "seed"),
    ("Technology", "列印", "打印", 0, "seed"),
    ("Technology", "印表機", "打印機", 0, "seed"),
    ("Technology", "掃瞄", "掃描", 9, ""),
    ("Technology", "連線", "連接", 10, "promoted 2026-09-21"),
    ("Technology", "通信", "通訊", 6, "promoted 2026-09-21"),
    ("Technology", "重置", "重設", 5, "promoted 2026-09-21"),
    ("Technology", "檢測", "偵測", 6, "promoted 2026-09-21"),
    # --- commerce and product news ---------------------------------------
    ("Commerce and product news", "預購", "預訂", 60, ""),
    ("Commerce and product news", "店鋪", "店舖", 28, ""),
    ("Commerce and product news", "店家", "店舖", 6, "mined as 推薦店家 -> 推薦店舖"),
    ("Commerce and product news", "套組", "套裝", 14, ""),
    ("Commerce and product news", "配件套組", "配件套裝", 5, ""),
    ("Commerce and product news", "塑料模型", "塑膠模型", 6, ""),
    ("Commerce and product news", "廠牌", "品牌", 5, ""),
    ("Commerce and product news", "聯名", "聯乘", 5, ""),
    ("Commerce and product news", "貼文", "帖文", 8, ""),
    ("Commerce and product news", "專案", "項目", 8, ""),
    ("Commerce and product news", "計畫", "計劃", 13, ""),
    ("Commerce and product news", "不含稅", "未連稅", 4, ""),
    ("Commerce and product news", "國定假日", "公眾假期", 9, ""),
    ("Commerce and product news", "日元", "日圓", 12, ""),
    ("Commerce and product news", "升息", "加息", 7, ""),
    ("Commerce and product news", "定價", "售價", 26, "promoted 2026-09-21"),
    ("Commerce and product news", "促銷價", "特價", 5, "promoted 2026-09-21"),
    ("Commerce and product news", "購入", "購買", 8, "promoted 2026-09-21"),
    ("Commerce and product news", "樣品", "樣本", 27, "promoted 2026-09-21"),
    ("Commerce and product news", "研發", "開發", 19, "promoted 2026-09-21"),
    ("Commerce and product news", "主體", "本體", 17, "promoted 2026-09-21"),
    ("Commerce and product news", "物件", "物業", 16, "promoted 2026-09-21; real-estate sense"),
    ("Commerce and product news", "東京遊戲展", "東京電玩展", 9, "promoted 2026-09-21"),
    # --- hobby, food, daily life ------------------------------------------
    ("Hobby, food, daily life", "手辦", "公仔", 4, "mined as 部手辦 -> 公仔"),
    ("Hobby, food, daily life", "抓娃娃機", "夾公仔機", 8, ""),
    ("Hobby, food, daily life", "壓克力", "亞克力", 20, ""),
    ("Hobby, food, daily life", "咖哩", "咖喱", 22, ""),
    ("Hobby, food, daily life", "巧克力", "朱古力", 8, ""),
    ("Hobby, food, daily life", "地瓜", "番薯", 7, ""),
    ("Hobby, food, daily life", "口香糖", "香口膠", 8, "mined as 口香糖 -> 口膠"),
    ("Hobby, food, daily life", "比基尼", "比堅尼", 5, ""),
    ("Hobby, food, daily life", "吸塵器", "吸塵機", 7, ""),
    ("Hobby, food, daily life", "匙圈", "匙扣", 6, ""),
    ("Hobby, food, daily life", "錢包", "銀包", 0, "added by hand 2026-09-21"),
    ("Hobby, food, daily life", "自行車", "單車", 14, ""),
    ("Hobby, food, daily life", "腳踏車", "單車", 0, "seed"),
    ("Hobby, food, daily life", "摩托車", "電單車", 6, ""),
    ("Hobby, food, daily life", "計程車", "的士", 0, "seed"),
    ("Hobby, food, daily life", "公車", "巴士", 0, "seed"),
    ("Hobby, food, daily life", "義大利", "意大利", 5, ""),
    ("Hobby, food, daily life", "北朝鮮", "北韓", 5, ""),
    ("Hobby, food, daily life", "公分", "厘米", 3, ""),
    ("Hobby, food, daily life", "公尺", "米", 0, "seed"),
    ("Hobby, food, daily life", "卡片遊戲", "卡牌遊戲", 12, "promoted 2026-09-21"),
    ("Hobby, food, daily life", "磁鐵", "磁石", 15, "promoted 2026-09-21"),
    ("Hobby, food, daily life", "空調", "冷氣", 11, "promoted 2026-09-21"),
    ("Hobby, food, daily life", "插畫家", "插畫師", 7, "promoted 2026-09-21"),
    ("Hobby, food, daily life", "插圖", "插畫", 5, "promoted 2026-09-21"),
    ("Hobby, food, daily life", "咖啡廳", "咖啡店", 5, "promoted 2026-09-21"),
    ("Hobby, food, daily life", "握把", "握柄", 5, "promoted 2026-09-21"),
    ("Hobby, food, daily life", "面料", "布料", 5, "promoted 2026-09-21"),
    # --- general wording ---------------------------------------------------
    ("General wording", "此次", "今次", 26, "promoted 2026-09-21"),
    ("General wording", "即便", "即使", 10, "promoted 2026-09-21"),
    ("General wording", "詳細資訊", "詳情", 12, "promoted 2026-09-21"),
    ("General wording", "基準", "標準", 12, "promoted 2026-09-21"),
    ("General wording", "白天", "日間", 5, "promoted 2026-09-21"),
    # --- orthography -------------------------------------------------------
    ("Orthography", "什麼", "甚麼", 52, ""),
    ("Orthography", "週年", "周年", 47, ""),
    ("Orthography", "裡", "裏", 219, "the one real HK character variant in the corpus"),
    # --- proper nouns: Hong Kong naming conventions ------------------------
    ("Proper nouns", "鋼彈", "高達", 49, "Gundam"),
    ("Proper nouns", "哥吉拉", "哥斯拉", 46, "Godzilla"),
    ("Proper nouns", "假面騎士", "幪面超人", 24, "Kamen Rider"),
    ("Proper nouns", "星際大戰", "星球大戰", 4, "Star Wars"),
    ("Proper nouns", "哆啦A夢", "多啦A夢", 5, "Doraemon"),
    ("Proper nouns", "弗里蓮", "芙莉蓮", 8, "Frieren"),
    ("Proper nouns", "英格拉姆", "英格倫", 5, "Patlabor Ingram"),
    ("Proper nouns", "超人巴羅姆", "超人巴洛姆", 53, ""),
    ("Proper nouns", "加魯魯蒙", "加魯魯獸", 10, "Digimon: HK renders -mon as 獸"),
    ("Proper nouns", "丰田", "豐田", 6, "Simplified leak in qwen output"),
    ("Proper nouns", "漫画", "漫畫", 5, "Simplified leak in qwen output"),
    ("Proper nouns", "大捜査線", "大搜查線", 7, "Japanese 捜 -> Chinese 搜"),
]

# Mined and real, but ruled out - each one is correct in some contexts and wrong in
# others, so it is left to the model rather than forced by a lookup table.
REJECTED = [
    ("上市", "發售", 25), ("製造商", "廠商", 15), ("獎品", "景品", 15),
    ("特價", "優惠價", 15), ("假日", "假期", 13), ("僅需", "只需", 11),
    ("總部", "本部", 9), ("尺碼", "尺寸", 8), ("工廠", "工場", 5),
    ("上漲", "上升", 6), ("零件", "配件", 15),
]

# --------------------------------------------------------------------------
# Simplified-output guard
#
# qwen-mt-flash returns a wholly Simplified block about 0.9% of the time, and the
# production DeepSeek path does it too (0.03% of blocks here, 0.28% by a looser
# measure). Re-sending the same source with the same settings fixed 29 of 30 cases,
# so it is flakiness, not a misconfiguration - the guard retries before it converts.
#
# A blanket OpenCC s2t pass is NOT safe as a fix: in legitimate Traditional text s2t
# also rewrites 台->臺, 周->週, 了->瞭, 岳->嶽, 布->佈 and 里->裏, which would corrupt
# correct output (and undo this table's own 週年 -> 周年). Conversion therefore runs
# only on a block that is overwhelmingly Simplified and only after a retry failed.
# --------------------------------------------------------------------------
_HAN = re.compile(r"[一-鿿]")
_CC = {}


def _opencc(name):
    if name not in _CC:
        import opencc
        _CC[name] = opencc.OpenCC(name)
    return _CC[name]


def _hits(text, converted):
    return sum(1 for a, b in zip(text, converted) if a != b and _HAN.match(a))


def looks_simplified(text):
    """True when a block came back in Simplified Chinese.

    Measured on 5,983 blocks: flags 0.89% of qwen-mt output and 0.03% of DeepSeek's,
    with no false positive on correct Traditional text.
    """
    if not text:
        return False
    han = [c for c in text if _HAN.match(c)]
    if not han:
        return False
    simplified_only = _hits(text, _opencc("s2t").convert(text))
    traditional_only = _hits(text, _opencc("t2s").convert(text))
    floor = 2 if len(han) < 15 else 4
    return simplified_only >= floor and simplified_only > traditional_only


def simplified_ratio(text):
    han = [c for c in text if _HAN.match(c)]
    if not han:
        return 0.0
    return _hits(text, _opencc("s2t").convert(text)) / len(han)


# OpenCC resolves one-to-many mappings to forms this corpus never uses. Measured over
# 409k characters of DeepSeek HK reference text: 台 225:0 臺, 群 36:0 羣, 秘 53:0 祕,
# 床 15:0 牀, 吃 64:0 喫, 才 97:0 纔, 了 1327:1 瞭, 岳 34:2 嶽, 托 16:13 託, 為 2097:0 爲.
# (着 is NOT in this list: the house style really is 着, 518:32 over 著.)
_OPENCC_TO_HOUSE = str.maketrans("臺羣祕牀喫纔瞭嶽託爲", "台群秘床吃才了岳托為")

# A Han character touching kana belongs to a Japanese name or title (花里みのり), where
# s2t's 里 -> 裏 would corrupt it. Those runs are left exactly as they are.
_KANA = re.compile(r"[぀-ゟ゠-ヿー]")
# the kana plus the few Han characters welded to it - a Japanese name, not the sentence
# around it, so the surrounding Chinese still gets converted
_JP_TOKEN = re.compile(
    r"([一-鿿A-Za-z0-9]{0,3}"
    r"(?:[぀-ゟ゠-ヿー]+[一-鿿A-Za-z0-9]{0,3})+)")


def force_traditional(text, min_ratio=0.15):
    """Last resort once a retry has already failed. Only touches text that is
    overwhelmingly Simplified, where s2t is the right tool and its one-to-many
    ambiguities cannot do more harm than leaving the block Simplified would.

    `min_ratio` is that "overwhelmingly" threshold. It exists because a blanket s2t
    over correct Traditional text rewrites 台, 周 and 岳, so the remote providers —
    which fail this way rarely and wholesale — keep the 0.15 floor.

    The local model is the exception: it mixes scripts inside a single block (来临
    beside 抵達), which measures 0.005-0.13 and so never trips that floor. It passes
    min_ratio=0. That is safe here rather than in general because the conversion was
    checked against 140 blocks of known-good local output and changed none of them.
    """
    if simplified_ratio(text) <= min_ratio:
        return text
    cc = _opencc("s2t")
    out = []
    for run in _JP_TOKEN.split(text):
        if not run:
            continue
        out.append(run if _KANA.search(run)
                   else cc.convert(run).translate(_OPENCC_TO_HOUSE))
    return "".join(out)


_TAGS = re.compile(r"(<[^>]+>)")

# Overrides the user has saved in the dashboard: {tw: {"hk": str, "enabled": bool}}.
# They add terms and can switch a built-in off; set_overrides() installs them.
_overrides: dict = {}
_MAP: dict = {}
_PATTERN = None


def _rebuild():
    global _MAP, _PATTERN
    table = {tw: hk for _c, tw, hk, _n, _note in APPLY}
    for tw, rule in _overrides.items():
        if rule.get("enabled", True) and rule.get("hk"):
            table[tw] = rule["hk"]
        else:
            table.pop(tw, None)
    _MAP = table
    # Longest first, so 插畫家 -> 插畫師 wins over 插圖 -> 插畫 on the same text.
    _PATTERN = (re.compile("|".join(re.escape(tw) for tw in sorted(table, key=len, reverse=True)))
                if table else None)


def set_overrides(rules):
    """Install the user's overrides. `rules` is an iterable of dicts with tw / hk / enabled."""
    global _overrides
    _overrides = {
        r["tw"]: {"hk": r.get("hk") or "", "enabled": bool(r.get("enabled", True))}
        for r in rules if r.get("tw")
    }
    _rebuild()


def active_terms():
    """Every substitution currently in force, as {tw: hk}."""
    return dict(_MAP)


def builtin_terms():
    return [{"category": c, "tw": tw, "hk": hk, "count": n, "note": note}
            for c, tw, hk, n, note in APPLY]


_rebuild()


def localize(text):
    """Rewrite Taiwan vocabulary as Hong Kong vocabulary. HTML tags are left alone."""
    if not text or _PATTERN is None:
        return text
    return "".join(
        part if part.startswith("<") else _PATTERN.sub(lambda m: _MAP[m.group(0)], part)
        for part in _TAGS.split(text)
    )


def finalize(text, retranslate=None):
    """Full post-translation pass for one block.

    `retranslate` is an optional zero-argument callable that re-requests the block from
    the translator; it is used once, only when the output came back Simplified.
    """
    if looks_simplified(text):
        if retranslate is not None:
            try:
                second = retranslate()
            except Exception:
                second = None
            if second and not looks_simplified(second):
                text = second
            else:
                text = force_traditional(second or text)
        else:
            text = force_traditional(text)
    return localize(text)


def stats():
    return {"builtin": len(APPLY), "overrides": len(_overrides),
            "active": len(_MAP), "rejected": len(REJECTED)}
