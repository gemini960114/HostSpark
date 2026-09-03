import re

_MODEL_EFFORT_SUFFIX_RE = re.compile(r"-(high|medium|low)$", re.IGNORECASE)

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

_RECUR_MINUTE_RE = re.compile(r"每\s*([0-9一二三四五六七八九十兩]{1,4})\s*分鐘")
_RECUR_HOUR_RE = re.compile(r"每\s*([0-9一二三四五六七八九十兩]{1,4})\s*(?:個)?小時")
_DAILY_TIME_RE = re.compile(r"每天.{0,6}?(\d{1,2})[:：](\d{2})")
_FULL_DATE_TIME_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日.{0,10}?(\d{1,2})[:：](\d{2})")
_BARE_TIME_RE = re.compile(r"(\d{1,2})[:：](\d{2})(?:[:：]\d{2})?")
_SCHEDULE_INTENT_RE = re.compile(
    r"排程|提醒我|叫我|通知我|跟我說|喊我|叫醒我|到時候提醒"
)


def compose_agy_prompt(user_text: str, rule_prompt: str) -> str:
    if not rule_prompt:
        return user_text
    return f"{rule_prompt}\n\n使用者請求：\n{user_text}"


def model_has_baked_in_effort(model: str | None) -> bool:
    """判斷模型名稱是否已內建推理深度（例如 `gemini-3.7-flash-high`、`gpt-oss-120b-medium`）。

    這類模型的名稱後綴本身就是 effort 等級，若同時再帶 `--effort` 給 agy，
    只要跟後綴不一致就會被 agy 拒絕（`--model X-medium conflicts with --effort=high`）。
    因此組裝 argv 時，這類模型一律不應該額外附加 `--effort` 旗標。
    """
    if not model:
        return False
    return bool(_MODEL_EFFORT_SUFFIX_RE.search(model.strip()))


def strip_effort_suffix(model: str | None) -> str | None:
    """去掉模型名稱尾端內建的 effort 後綴（-high/-medium/-low）。

    用來把舊資料正規化成現在的基底名稱，例如切換到新版「model 選單只顯示
    3 個基底名稱」之前，某個 chat 可能還存著 `gemini-3.8-flash-medium`
    這種帶後綴的舊值；要判斷它「現在對應哪個基底模型按鈕」時，
    先用這個函式去掉後綴再比對，才不會因為字串不完全相等而找不到打勾對象。
    """
    if not model:
        return model
    return _MODEL_EFFORT_SUFFIX_RE.sub("", model.strip())


# 這幾個「基底名稱」在 AGY 的模型目錄裡，實際上只以 `<base>-<effort>`
# （例如 gemini-3.8-flash-medium）註冊，沒有不帶後綴的獨立模型可選。
# UI 上把 model 跟 effort 拆成兩個各自獨立的選項比較好選，但真正呼叫
# agy 時，這幾個基底名稱必須把 effort 併回模型字串裡才是有效的 --model 值。
EFFORT_VARIANT_MODEL_BASES = frozenset({
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
})


def resolve_model_and_effort_args(model: str | None, effort: str | None) -> list[str]:
    """組出要傳給 agy 的 `--model` / `--effort` 參數。

    - 若 model 是 EFFORT_VARIANT_MODEL_BASES 裡的基底名稱且有設定 effort，
      直接組成 `--model <base>-<effort>`（例如 gemini-3.8-flash + medium
      → `--model gemini-3.8-flash-medium`），不額外帶 `--effort`。
    - 若 model 本身已內建 effort 後綴（model_has_baked_in_effort），
      只送 `--model`，不送 `--effort`，避免衝突。
    - 其餘情況維持原本行為：model 跟 effort 各自獨立帶出。
    """
    args: list[str] = []
    if model:
        if effort and model in EFFORT_VARIANT_MODEL_BASES:
            args.extend(["--model", f"{model}-{effort}"])
            return args
        args.extend(["--model", model])
        if effort and not model_has_baked_in_effort(model):
            args.extend(["--effort", effort])
        return args
    if effort:
        args.extend(["--effort", effort])
    return args


def _cn_num_to_int(token: str) -> int | None:
    """把「五」、「十五」、「二十」這類中文數字（或阿拉伯數字字串）轉成 int，失敗回傳 None。"""
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if "十" in token:
        tens_part, _, ones_part = token.partition("十")
        tens = _CN_DIGITS.get(tens_part, 1) if tens_part else 1
        ones = _CN_DIGITS.get(ones_part, 0) if ones_part else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(token)


def detect_schedule_intent(text: str) -> tuple[str, str] | None:
    """偵測純文字中的排程／未來時間意圖，回傳 `(cron_expr, task_text)`；偵測不到則回傳 None。

    這是路由層的**決定性**攔截，不依賴 AGY／LLM 自行判斷——命中時訊息不會被送去給
    AGY 執行單次對話，而是直接餵給既有的 `/schedule_add` 建立流程（保留 AGY 整理 prompt
    ＋ Telegram 按鈕二次確認），避免 AGY 為了「等到那個時間」而在單次非互動呼叫中卡住，
    佔用全域任務佇列，也不需要使用者自己複製貼上指令再送一次。
    「每 N 分鐘／小時」這類重複性描述本身已是清楚訊號，不需要額外的意圖關鍵字；
    單一日期／時間點的描述則需搭配「排程」「提醒我」等意圖關鍵字才觸發，降低誤判。
    """
    if not text:
        return None
    stripped = text.strip()

    m = _RECUR_MINUTE_RE.search(text)
    if m:
        n = _cn_num_to_int(m.group(1))
        if n and 1 <= n <= 59:
            return f"*/{n} * * * *", stripped

    m = _RECUR_HOUR_RE.search(text)
    if m:
        n = _cn_num_to_int(m.group(1))
        if n and 1 <= n <= 23:
            return f"0 */{n} * * *", stripped

    has_intent = bool(_SCHEDULE_INTENT_RE.search(text))

    if has_intent:
        m = _DAILY_TIME_RE.search(text)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{mm} {hh} * * *", stripped

        m = _FULL_DATE_TIME_RE.search(text)
        if m:
            month, day, hh, mm = (int(g) for g in m.groups())
            if 1 <= month <= 12 and 1 <= day <= 31 and 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{mm} {hh} {day} {month} *", stripped

        m = _BARE_TIME_RE.search(text)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{mm} {hh} * * *", stripped

    return None
