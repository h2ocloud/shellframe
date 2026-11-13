"""What went wrong, in plain words, and what to do next.

Every failure the core reports has a `code`. The words for it are here (English and Traditional Chinese),
each with a "next": the thing the person can do. A service's description can add or replace any of them
(`problems`, or a route's `test.verdicts` for what a test came to). A message never carries a command's
output, a path with a user name, a token or an exception's text: it is these sentences and nothing else.
"""


def _t(en, zh):
    return {"en": en, "zh": zh}


CORE = {
    # asking for something that is not there
    "unknown_service": {"text": _t("ShellBot does not know that service.", "ShellBot 不認得這個服務。"),
                        "next": _t("Open Settings → Connections and pick one from the list.", "到「設定 → 連接」，從清單裡選一個。")},
    "unknown_route": {"text": _t("That way of connecting is not offered for this service.", "這個服務沒有這種接法。"),
                      "next": _t("Pick one of the ways shown on the card.", "選卡片上列出的接法。")},
    "unknown_step": {"text": _t("That step does not exist.", "沒有這一步。"),
                     "next": _t("Go back to the card and use its buttons.", "回到卡片，用上面的按鈕。")},
    "unknown_action": {"text": _t("That button does nothing for this step.", "這一步沒有這個按鈕的功能。"),
                       "next": _t("Close the card and open it again.", "關掉卡片再重開。")},
    "unknown_check": {"text": _t("ShellBot has no check by that name.", "ShellBot 沒有這個檢查。"),
                      "next": _t("Update ShellBot and try again.", "把 ShellBot 更新到最新再試。")},
    "bad_request": {"text": _t("The request was not understood.", "這個要求看不懂。"),
                    "next": _t("Close the card and open it again.", "關掉卡片再重開。")},
    "not_started": {"text": _t("This service is not being connected yet.", "這個服務還沒開始接。"),
                    "next": _t("Press Connect to start.", "按「連接」開始。")},
    "no_adapter": {"text": _t("This step needs a part of ShellBot that is not installed.", "這一步需要的 ShellBot 元件沒有裝到。"),
                   "next": _t("Update ShellBot, then try again.", "更新 ShellBot 後再試一次。")},
    "adapter_error": {"text": _t("This step stopped because of a problem inside ShellBot.", "這一步在 ShellBot 裡面出了問題，停住了。"),
                      "next": _t("Try the step again. If it happens again, press 'I'm stuck' to ask a bot.", "再試一次。還是不行就按「我卡住了」問 bot。")},
    "step_blocked": {"text": _t("This step is not finished yet.", "這一步還沒完成。"),
                     "next": _t("Do what the step says, then press 'I did it'.", "照這一步寫的做完，再按「我做好了」。")},
    "check_failed": {"text": _t("ShellBot could not confirm this step.", "ShellBot 確認不了這一步。"),
                     "next": _t("Check what the step asked for, then press 'I did it' again.", "對照這一步要你做的事，再按一次「我做好了」。")},
    "scope_not_allowed": {"text": _t("That permission is not one this service may ask for.", "這項權限不在這個服務可以要的範圍裡。"),
                          "next": _t("Pick only the permissions listed on the card.", "只選卡片上列出的權限。")},
    # the AI programs
    "cli_missing": {"text": _t("That AI program is not installed on this computer.", "這台電腦沒有安裝那個 AI 程式。"),
                    "next": _t("Install it from Settings → Accounts, or leave it out.", "到「設定 → 帳號」安裝，或先不接它。")},
    "cli_unsupported": {"text": _t("That AI program has no way to take this kind of connection.", "那個 AI 程式沒有辦法接這種連線。"),
                        "next": _t("Use a Claude tab for this service, or another way listed on the card.", "這個服務請用 Claude 分頁，或卡片上列的其他接法。")},
    "cli_manual": {"text": _t("That AI program has to be set up by hand.", "那個 AI 程式要自己手動設定。"),
                   "next": _t("Follow the lines shown under its name.", "照它名字下面列出的內容填。")},
    "cli_failed": {"text": _t("The AI program refused the change.", "AI 程式沒有接受這個設定。"),
                   "next": _t("Close that AI's tabs, then try again. If it still fails, press 'I'm stuck'.", "先關掉那個 AI 的分頁再試一次。還是不行就按「我卡住了」。")},
    "cli_entry_missing": {"text": _t("The connection is no longer in that AI program's settings.", "那個 AI 程式的設定裡已經沒有這個連線了。"),
                          "next": _t("Connect again; the steps you already finished are kept.", "重新接上；做過的步驟會保留。")},
    "cli_foreign_entry": {"text": _t("ShellBot only changes the entries it made itself, and that one is not its own.", "ShellBot 只改自己建的項目，那一個不是它建的。"),
                          "next": _t("Leave it as it is.", "維持原樣就好。")},
    # secrets
    "secret_store_unsupported": {"text": _t("This computer has no safe place ShellBot can keep a key.", "這台電腦沒有 ShellBot 能安全放金鑰的地方。"),
                                 "next": _t("Use a way of connecting that needs no key, if the card offers one.", "卡片上有不需要金鑰的接法就改用那個。")},
    "secret_store_failed": {"text": _t("The key could not be saved to the system's key store.", "金鑰沒辦法存進系統的金鑰圈。"),
                            "next": _t("Unlock the keychain or the credential manager and try again.", "把鑰匙圈或認證管理員解鎖後再試一次。")},
    "secret_missing": {"text": _t("The key is no longer in the system's key store.", "金鑰已經不在系統的金鑰圈裡了。"),
                       "next": _t("Enter the key again.", "重新輸入一次金鑰。")},
    "secret_empty": {"text": _t("Nothing was entered.", "沒有輸入任何東西。"),
                     "next": _t("Paste the key into the box, then press Save.", "把金鑰貼進輸入框，再按「儲存」。")},
    # the service itself
    "token_expired": {"text": _t("The sign-in has expired.", "登入已經過期。"),
                      "next": _t("Connect again.", "重新接上。")},
    "token_revoked": {"text": _t("The service no longer allows this connection (it was revoked or removed).", "服務那邊已經不允許這個連線（被撤銷或刪除了）。"),
                      "next": _t("Connect again.", "重新接上。")},
    "expired": {"text": _t("The sign-in has expired.", "登入已經過期。"),
                "next": _t("Connect again.", "重新接上。")},
    "revoked": {"text": _t("The service no longer allows this connection (it was revoked or removed).", "服務那邊已經不允許這個連線（被撤銷或刪除了）。"),
                "next": _t("Connect again.", "重新接上。")},
    "unreachable": {"text": _t("ShellBot could not reach the service just now.", "ShellBot 剛剛連不到這個服務。"),
                    "next": _t("Check the network. Nothing needs to be redone yet.", "看一下網路。目前不用重接。")},
    # tests
    "test_failed": {"text": _t("The test did not go through.", "測試沒有通過。"),
                    "next": _t("Look at the step named under the result, fix it, then test again.", "看結果下面寫的那一步，修好再測一次。")},
    "test_timeout": {"text": _t("The test took too long.", "測試等太久了。"),
                     "next": _t("Try again. If it happens again, press 'I'm stuck'.", "再測一次。還是這樣就按「我卡住了」。")},
    "test_not_ready": {"text": _t("The test could not start yet.", "測試還不能開始。"),
                       "next": _t("Finish the steps above first.", "先把上面的步驟做完。")},
    "test_cancelled": {"text": _t("The test was stopped.", "測試已經停止。"),
                       "next": _t("Press Test to run it again.", "按「測試」再跑一次。")},
    "needs_user": {"text": _t("The test is waiting for you.", "測試在等你操作。"),
                   "next": _t("Do what the test asks, and it goes on by itself.", "照測試要求的做，它會自己繼續。")},
    # ShellBot's own file
    "state_write_failed": {"text": _t("ShellBot could not save where you are in these steps.", "ShellBot 存不了你做到哪一步。"),
                           "next": _t("Check that the disk is not full, then try again.", "確認磁碟沒滿，再試一次。")},
}

GENERIC = {"text": _t("Something did not work.", "有地方沒成功。"),
           "next": _t("Try again. If it keeps happening, press 'I'm stuck' to ask a bot.", "再試一次。一直這樣就按「我卡住了」問 bot。")}


def explain(code: str, desc: dict = None, route: dict = None) -> dict:
    """{"code", "text": {en, zh}, "next": {en, zh}} for a code. A route's test verdicts win over the
    service's `problems`, which win over the core's words; an unknown code gets the generic sentence."""
    code = str(code or "")
    for source in ((route or {}).get("test", {}).get("verdicts"), (desc or {}).get("problems"), CORE):
        hit = (source or {}).get(code)
        if isinstance(hit, dict) and hit.get("text"):
            return {"code": code, "text": hit["text"], "next": hit.get("next") or GENERIC["next"]}
    return {"code": code, "text": GENERIC["text"], "next": GENERIC["next"]}
