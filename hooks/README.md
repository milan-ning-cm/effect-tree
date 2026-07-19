# 全自動維護(可選):session 結束 hook

目標:**每個專案自動有樹、人跟 agent 對話樹就自己長**——人不用記得建、不用記得維護。

以 Claude Code 為例,在 settings.json 的 `hooks.Stop` 掛 `auto_tree_hooks.py`(路徑自行對應):

```json
{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python <repo路徑>/hooks/auto_tree_hooks.py"}]}]}}
```

兩個迴圈:
1. **機械迴圈(零 LLM 成本)**:每次對話結束,替 `projects/` 底下每個還沒有樹的資料夾照 `TEMPLATE.md` 自動建 `_效果樹.md`(冪等;資料夾放空的 `.no_tree` 檔=退出)。
2. **認知迴圈**:偵測本次對話動過哪個專案的檔案,輸出提醒讓 agent 在收尾時同步那棵樹(狀態推進/掛新節點/補樹根)。

其他 agent 框架:概念相同——「session 結束時:補建缺樹+點名同步動過的專案」,照 `auto_tree_hooks.py` 的邏輯移植即可。
