#!/usr/bin/env python3
"""The paired-computer detail panel must render through the locale table."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
failures = []

for old in (
    "連線中", "斷開配對", "主控", "受控", "互傳訊息", "傳檔案",
    "編輯位址", "無位址", "載入遠端 session", "新增 session",
    "行動裝置", "在「${esc2(peer.name)}」開新 session", "傳訊息給 ${peer.name}",
    "把檔案拖到這裡傳給", "斷開與「${esc2(peer.name)}」的配對？",
    "編輯「${esc2(peer.name)}」的位址",
):
    if old in index:
        failures.append(f"hard-coded peer-detail text remains: {old}")

if "remoteTabTitle" not in index or "peerReachable" not in index:
    failures.append("peer detail does not use locale keys")
if "messagesTitle" not in index or "filesTitle" not in index:
    failures.append("message/file panels do not use locale keys")

if failures:
    print("FAIL")
    print("\n".join(failures))
    raise SystemExit(1)
print("PASS: paired-computer detail UI uses i18n")
