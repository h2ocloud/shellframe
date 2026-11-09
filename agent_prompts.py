"""The prompts ShellFrame writes into an agent's tab when it hands it work or asks
it to wrap up: the roster delegation prompt and the idle-reaper summary prompt.

Both exist in English and in Traditional Chinese; sf_lang.prompt_lang() (the OS
language) picks one. Keep the two versions saying the same thing. The bot
prompts live in bots.py and the A2A banners in agent_link.py, under the same rule.

Leaf module: no `import main`.
"""

import sf_lang

_DELEGATE = {
    "en": {
        "head": "You are the \"{label}\" worker.\nYour job: {responsibility}\n\n"
                "This task comes from the ShellBot coordinator. Stay within your own job; "
                "do not take over another worker's area.\n\nTask:\n{task}\n\n",
        "default_job": "handle the task the coordinator assigns",
        "tagged": "The task tags other tabs (other agent sessions) you need to work with:\n{lines}\n"
                  "Working with the tagged tabs is a required part of this task, not optional:\n"
                  "- First run `sfctl peek <sid> --lines 60` to see the tab's current state and context, then act.\n"
                  "- Use `sfctl send <sid> '<message>'` to ask that tab's agent something, give it an instruction or "
                  "hand something over; after sending, peek again to confirm it got it and answered, waiting or "
                  "following up if needed.\n"
                  "- Your report must include how it went with each tagged tab; refer to tabs by label (#name); "
                  "sids are only for sfctl.\n\n",
        "rules": (
            "Working rules:\n"
            "- First check the context and current state you need; avoid creating, sending or overwriting things twice.\n"
            "- When looking for files, start from known project paths; do not sweep /Users, ~/Library, "
            "~/Library/Mobile Documents, Mail, Messages, Photos or other protected macOS folders, which trigger "
            "privacy prompts. If you cannot find a path, report back and ask the coordinator for context.\n"
            "- Do anything others can see only when the user's text clearly authorizes it; otherwise dry-run first "
            "or report that it needs confirmation.\n"
            "- If the task needs another worker, report back so the coordinator can reassign it; do not widen the "
            "scope yourself.\n"
            "- If your output is something the user can use directly (a draft, a report, a lookup result, an "
            "operational conclusion), reply first in a ready-to-paste form so the coordinator can pass it on at "
            "once, without waiting for other parallel work.\n"
            "- When done, reply with: the ready-to-paste content, the result, verification, what changed or was "
            "sent, blockers, and whether this should become memory/skill/docs.\n"
            "\nStatus light (detected automatically; you usually do not report it):\n"
            "- ShellBot sets this tab's light from your actual activity (tool calls, turn start and end, the "
            "screen): blue 🔵 while running, green 🟢 when the turn ends. "
            "**You no longer need to print `[[SF:WORKING]]` or `[[SF:GREEN]]`**; just do the work.\n"
            "- Only two states detection cannot see remain as optional hints (each on its own line, nothing "
            "before or after it):\n"
            "  - The user or coordinator must decide → `[[SF:RED]]` → 🔴 red, followed by a numbered menu (the "
            "options are the decision), so Telegram can push the options to the user.\n"
            "  - Blocked on something outside (waiting for a person, another team, an external event detection "
            "cannot see) → `[[SF:YELLOW:one-line reason]]` → 🟡 yellow, and the reason is pushed to the user.\n"
            "- These are hints, not status reports; if unsure, do not print them, since working/done are covered "
            "by detection. A decision turn still ends with a numbered menu for the user to choose from.\n"
            "\nClosing rules (must follow):\n"
            "- Every time you finish a task or need the user's or coordinator's decision, end the turn with a "
            "numbered menu (with the GREEN/RED light above), so ShellBot detects it as waiting for a decision and "
            "pushes the options to the user as Telegram buttons. Do not end with plain text and go idle.\n"
            "- Menu format, strictly: at least 2 items, each on its own line starting with \"number.\" or "
            "\"number)\", consecutive, with no other text or blank lines in between, for example:\n"
            "1. Close this tab (task done)\n"
            "2. More changes: <details>\n"
            "- Save memory first, then you may be closed: before offering \"close this tab\", make sure this "
            "task's insights, learnings and operational gotchas are written to memory "
            "(~/.claude/projects/<project slug>/memory/), or note next to that option \"nothing to remember for "
            "this task\". This is also the precondition for GREEN: green only after memory is saved.\n"
        ),
    },
    "zh": {
        "head": "你是「{label}」worker。\n職責：{responsibility}\n\n"
                "這是 ShellFrame 總控派工。請維持自己的職責邊界，不要主動接手其他 worker 的領域。\n\n"
                "任務：\n{task}\n\n",
        "default_job": "依總控派工處理指定任務",
        "tagged": "任務中用 # 標註了需要互動的 tab（其他 agent session）：\n{lines}\n"
                  "與被標註 tab 互動是本任務的必要環節，不是可選項：\n"
                  "- 先 `sfctl peek <sid> --lines 60` 了解該 tab 目前狀態與上下文，再行動。\n"
                  "- 用 `sfctl send <sid> '<訊息>'` 對該 tab 的 agent 提問、下指令或交接；送出後再 peek 確認對方收到並回應，必要時等待或追問。\n"
                  "- 回報時務必包含與各標註 tab 的互動結果；提及 tab 用 label（#名稱），sid 只用在 sfctl 指令。\n\n",
        "rules": (
            "工作規則：\n"
            "- 先確認需要的上下文與現有狀態；避免重複建立、重複送出或覆蓋。\n"
            "- 查檔案時先限定已知專案路徑；不要廣掃整個 /Users、~/Library、~/Library/Mobile Documents、Mail、Messages、Photos 等 macOS 受保護資料夾，避免觸發系統隱私權限彈窗。找不到路徑時先回報需要總控補上下文。\n"
            "- 若是外部可見操作，只有在使用者文字已明確授權時才送出；否則先 dry-run 或回報需要確認。\n"
            "- 若任務需要其他 worker，回報總控改派，不要自行擴張範圍。\n"
            "- 若產出是可直接給使用者的草稿、報告、查詢結果或操作結論，先用「可直接轉貼」格式回覆，讓總控能立即轉交，不要等其他平行工作完成。\n"
            "- 完成後回覆：可直接轉貼內容、結果、驗證、變更/送出項目、阻塞、是否建議納入 memory/skill/docs。\n"
            "\n燈號（自動偵測，通常不需自報）：\n"
            "- 本 tab 燈號由 ShellFrame 從你的『實際活動』自動判定（工具呼叫、回合起訖、畫面）：執行中自動亮 🔵、回合結束自動轉 🟢。"
            "**不需要再印 `[[SF:WORKING]]` 或 `[[SF:GREEN]]`**，專心做事即可。\n"
            "- 只有兩個『偵測看不出來』的狀態保留為可選提示（要用時自成一行、前後不接其他文字）：\n"
            "  - 需要使用者／總控決策 → `[[SF:RED]]` → 🔴紅，並接編號選單（選項即決策內容），讓 TG 把選項推給使用者。\n"
            "  - 卡在『外部條件』（等人回覆、等他隊、等外部事件等偵測看不到的）→ `[[SF:YELLOW:一句話原因]]` → 🟡黃，原因推給使用者。\n"
            "- 這兩個是提示不是狀態回報；不確定就不要印，working／done 由偵測涵蓋。決策回合仍要附編號選單供使用者選擇。\n"
            "\n收尾規則（務必遵守）：\n"
            "- 每次『完成任務』或『需要使用者／總控決策』時，回合最後務必輸出一個編號選項選單（搭配上面 GREEN／RED 燈號），"
            "讓 ShellFrame 偵測為待決策、把選項以 TG 按鈕推給使用者。不要只用純文字結尾後 idle。\n"
            "- 選單格式硬規則：至少 2 項，每項自成一行、行首為「數字.」或「數字)」，連續排列、中間不夾其他文字或空行，例如：\n"
            "1. 回收此 tab（任務完成）\n"
            "2. 還要調整：<說明>\n"
            "- 『先沉澱記憶，才可被回收』：在選單提供『回收此 tab』選項前，必須先確認已把本次洞察／學習／"
            "操作 gotcha 寫入 memory（~/.claude/projects/<專案 slug>/memory/），或在該選項旁註明「此任務無需記憶」。"
            "這也是 GREEN 的前提——記憶沉澱完才給綠燈。\n"
        ),
    },
}


def delegate(label: str, responsibility: str, task: str, tagged=None, user_prompt: str = "",
             lang: str = None) -> str:
    """The message a roster worker receives when work is delegated to it."""
    T = _DELEGATE[lang or sf_lang.prompt_lang()]
    prompt = T["head"].format(label=label, responsibility=responsibility or T["default_job"],
                              task=(task or "").strip())
    if tagged:
        lines = "\n".join(f"- #{t['label']} → sid {t['sid']}" for t in tagged)
        prompt += T["tagged"].format(lines=lines)
    prompt += T["rules"]
    if user_prompt:
        prompt += "\n## User Instructions (excerpt)\n\n" f"{user_prompt}\n"
    return prompt


def idle_summary(label: str, idle_sec: int, reflection_file: str = "", lang: str = None) -> str:
    """What the idle reaper asks a tab before closing it. `reflection_file` set means
    the tab is also asked to append a short reflection to that shared file."""
    if (lang or sf_lang.prompt_lang()) == "en":
        sediment = (
            "\nKeep what is worth keeping: if this session has a workflow, preference or long-lived project "
            "fact worth keeping, append a short reflection to this shared reflections file after the summary:\n"
            f"{reflection_file}\n"
            "Only append to that file; do not edit the shared memory hub or private memory directly. "
            "If nothing is worth keeping, write none in the summary with a one-line reason.\n"
        ) if reflection_file else ""
        return (
            "ShellBot's idle scheduler is about to close this session.\n"
            f"Session: {label or 'unnamed'}\n"
            f"Idle for: about {idle_sec} seconds\n\n"
            "Before it closes, write a short summary and retrospective, in the language of this conversation.\n"
            "Include:\n"
            "1. What this session accomplished.\n"
            "2. What is unfinished, or the risks.\n"
            "3. Whether this should become a skill, memory, or none, and why.\n"
            f"{sediment}"
            "Apart from that append, do nothing others can see and nothing destructive.\n"
        )
    sediment = (
        "\n沉澱要求：若這個 session 有值得保留的流程、偏好或專案長期事實，"
        "請在輸出總結後，追加一段精簡反思到下列共用反思主檔：\n"
        f"{reflection_file}\n"
        "只寫 Agent Reflections.md；不要直接改 Agent Memory Hub 或私有 memory。"
        "若不值得沉澱，請在總結中寫 none 並用一句話說明。\n"
    ) if reflection_file else ""
    return (
        "ShellFrame 閒置排程即將關閉這個 session。\n"
        f"Session：{label or 'unnamed'}\n"
        f"閒置秒數：約 {idle_sec}\n\n"
        "請在關閉前輸出一份簡短總結與複盤，使用繁體中文。\n"
        "請包含：\n"
        "1. 這個 session 完成了什麼。\n"
        "2. 尚未完成事項或風險。\n"
        "3. 是否建議沉澱為 skill、memory 或 none，並說明原因。\n"
        f"{sediment}"
        "除上述共用反思主檔追加外，請不要執行其他外部可見或破壞性操作。\n"
    )
