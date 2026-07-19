#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""效果樹自動維護 hook(Claude Code Stop hook 範例;其他框架照邏輯移植)。

兩個迴圈:
  A) 機械:projects/ 底下每個沒有 _效果樹.md 的資料夾,照 TEMPLATE 自動建(冪等;
     放 .no_tree 空檔=該專案退出)。零 LLM 成本。
  B) 認知:本次對話有 Write/Edit 過某專案的檔案(樹本身除外)→ 輸出
     {"decision":"block","reason":...} 提醒 agent 收尾時同步那棵樹。

契約:stdin 收 Claude Code hook JSON;要讓 agent 行動就印 block JSON 後 exit 0;
內部任何錯誤一律吞掉(exit 0),hook 故障不得擋住使用者。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # repo 根
PROJECTS = os.path.join(ROOT, "projects")
TEMPLATE = os.path.join(ROOT, "TEMPLATE.md")
TREE = "_效果樹.md"
FILE_PATH_RE = re.compile(r'"file_path"\s*:\s*"([^"]+)"')


def ensure_trees():
    """迴圈 A:補建缺樹。永不拋錯。"""
    try:
        if not (os.path.isdir(PROJECTS) and os.path.exists(TEMPLATE)):
            return
        with open(TEMPLATE, encoding="utf-8") as f:
            tpl = f.read()
        for name in os.listdir(PROJECTS):
            d = os.path.join(PROJECTS, name)
            if not os.path.isdir(d) or name.startswith((".", "_")):
                continue
            if os.path.exists(os.path.join(d, ".no_tree")):
                continue
            md = os.path.join(d, TREE)
            if not os.path.exists(md):
                with open(md, "w", encoding="utf-8", newline="\n") as f:
                    f.write(tpl.replace("<專案名>", name))
    except Exception:
        pass


def touched_projects(transcript):
    """本次對話寫過哪些專案的檔案(樹本身不算——改樹是維護不是新工作)。
    transcript JSON 中 CJK 常被寫成 \\uXXXX,一律用 json.loads 還原,別自己拼 replace。"""
    prefix = PROJECTS.replace("\\", "/").lower().rstrip("/") + "/"
    hits = set()
    for m in FILE_PATH_RE.finditer(transcript):
        try:
            p = json.loads('"' + m.group(1) + '"')
        except Exception:
            p = m.group(1)
        p = p.replace("\\", "/")
        if not p.lower().startswith(prefix):
            continue
        rest = p[len(prefix):]
        if "/" not in rest:
            continue
        name, fname = rest.split("/", 1)
        if name and fname != TREE:
            hits.add(name)
    return sorted(hits)


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "ignore"))
    except Exception:
        data = {}
    ensure_trees()
    if data.get("stop_hook_active"):          # 防無限迴圈:hook 觸發的回合不再觸發
        return
    tp = data.get("transcript_path", "")
    transcript = ""
    if tp and os.path.exists(tp):
        try:
            with open(tp, encoding="utf-8", errors="ignore") as f:
                transcript = f.read()
        except Exception:
            pass
    projs = touched_projects(transcript)
    if projs:
        print(json.dumps({"decision": "block", "reason": (
            "[效果樹同步] 本次對話動過專案:" + "、".join(projs) + "。"
            "請打開各專案的 _效果樹.md 同步:①這次做的事對應哪些節點→狀態該推進的推進"
            "(大成果推到待驗收,完成權在使用者);②新效果/執行/背景掛上去;"
            "③樹根還是模板佔位就依脈絡補填。無實質進展就一句話略過,不硬湊。"
        )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
