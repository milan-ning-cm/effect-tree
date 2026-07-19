#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""效果樹 — 從終端效果往回推必然前提的專案樹(md 真相源 + 可拖拉 SVG 視圖)。

真相源是各專案的 _效果樹.md;本服務只做「解析 + 整區重寫」:
- GET  /             → tree.html
- GET  /api/projects → 自動掃描 projects/*/_效果樹.md
- GET  /api/doc?f=   → {token, forest[], errors[]}
- GET  /api/mtime?f= → {token, version}   (前端輪詢自動刷新)
- POST /api/save     → {file, expect_token, forest}  (token 不符回 409)

只改寫 <!-- effect-tree:start/end --> 標記之間;標記外內容 byte 不動。
用法: py tree.py [md路徑] [--port 8778] [--no-browser] [--projects-dir <路徑>]
不帶參數=自動掃描模式;帶路徑=單檔模式(檔案不存在會照 TEMPLATE 自動建立)。
--projects-dir=換一個專案根目錄做自動掃描(預設=本 repo 的 1_Projects);
寫入白名單與 /api/create 都跟著錨到該目錄,單一 instance 只認一個根。
"""
import argparse
import base64
import hashlib
import json
import re
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ThreadingHTTPServer 每連線一執行緒(單執行緒會被瀏覽器 preconnect 楔死),檔案讀寫加全域鎖
IO_LOCK = threading.Lock()

VERSION = "v29"
HERE = Path(__file__).resolve().parent
PROJECTS_DIR = HERE / "projects"   # 獨立版:專案樹放 repo 內 projects/;可用 --projects-dir 改
TREE_FILENAME = "_效果樹.md"

START_MARK = "<!-- effect-tree:start -->"
END_MARK = "<!-- effect-tree:end -->"

NODE_RE = re.compile(
    r"^(\s*)- ([EXC]\d+[a-z]?)\[(效果|執行|Context)\|([^\]|]*)\]\s*(.*?)"
    r"(?:\s*\{(判準|產出|來源):\s*(.*?)\})?(?:\s*\{圖:\s*(.*?)\})?\s*$")
# 型別↔ID前綴↔附註欄名;Context=背景脈絡節點(非狀態非動作,不參與前沿推導)
TYPE_PREFIX = {"效果": "E", "執行": "X", "Context": "C"}
TYPE_NOTEKEY = {"效果": "判準", "執行": "產出", "Context": "來源"}
STATUSES = ["待動", "進行中", "待驗收", "完成", "擱置"]
# 別的 agent 手寫 md 常出現的狀態別名——解析時自動歸一,別鎖檔(實踩:loop E 同步寫出「已完成」整檔被鎖)
STATUS_ALIAS = {"已完成": "完成", "執行中": "進行中", "進行": "進行中",
                "待辦": "待動", "待做": "待動", "暫停": "擱置", "已擱置": "擱置"}
ID_RE = re.compile(r"^[EXC]\d+[a-z]?$")  # 允許 X3a 型子編號(agent 手寫變體)
ASSETS_DIRNAME = "_效果樹_assets"       # 貼上的截圖存樹旁邊這個資料夾,md 用 {圖: 檔名} 引用
IMG_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def discover_trees():
    if not PROJECTS_DIR.is_dir():
        return []
    found = sorted(PROJECTS_DIR.glob("*/" + TREE_FILENAME),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.parent.name, "path": str(p)} for p in found]


class Doc:
    """效果樹檔案的載入/解析/標記區整段重寫。"""

    def __init__(self, path):
        self.path = Path(path)
        self.newline = "\n"

    def token(self):
        # 版本 token=內容雜湊;Windows 剛寫完的檔 mtime 會延遲更新,不可當 token
        return hashlib.blake2b(self.path.read_bytes(), digest_size=12).hexdigest()

    def read_lines(self):
        raw = self.path.read_bytes().decode("utf-8")
        self.newline = "\r\n" if "\r\n" in raw else "\n"
        return raw.split(self.newline)

    def write_lines(self, lines):
        self.path.write_bytes(self.newline.join(lines).encode("utf-8"))

    def _marker_span(self, lines):
        """回傳 (start_idx, end_idx) = 標記行本身的行號;找不到回 None。"""
        s = e = None
        for i, ln in enumerate(lines):
            if ln.strip() == START_MARK and s is None:
                s = i
            elif ln.strip() == END_MARK and s is not None:
                e = i
                break
        return (s, e) if s is not None and e is not None else None

    def parse(self):
        lines = self.read_lines()
        span = self._marker_span(lines)
        errors, forest = [], []
        if span is None:
            return {"token": self.token(), "file": str(self.path),
                    "name": self.path.parent.name, "forest": [],
                    "errors": [{"line": 0, "text": f"找不到 {START_MARK} / {END_MARK} 標記,請照 TEMPLATE 補上"}]}
        # 重複 ID 自癒:先掃全區每前綴最大號,撞號的「後出現者」自動改下一個新號
        # (多 agent 各自加節點撞號是機械可修問題,鎖檔會把整棵樹卡死——容錯歸一別鎖檔)
        counters = {"E": 0, "X": 0, "C": 0}
        for i in range(span[0] + 1, span[1]):
            m = re.match(r"^\s*- ([EXC])(\d+)", lines[i])
            if m:
                counters[m.group(1)] = max(counters[m.group(1)], int(m.group(2)))
        seen_ids = set()
        stack = []  # [(level, node)]
        for i in range(span[0] + 1, span[1]):
            line = lines[i]
            if not line.strip():
                continue
            # 一行兩個以上附註欄(如同掛 {判準:}{產出:})超出規格——照設計哲學進 errors 鎖編輯,
            # 不靜默縫合改寫語意(v24 會把兩塊縫成一個 {產出:},roundtrip 不穩定)
            if len(re.findall(r"\{(?:判準|產出|來源)\s*:", line)) > 1:
                errors.append({"line": i + 1,
                               "text": f"一行只能有一個附註欄({line.strip()[:60]})——請拆成子節點或手動合併"})
                continue
            m = NODE_RE.match(line)
            if not m:
                errors.append({"line": i + 1, "text": line.strip()[:80]})
                continue
            indent, nid, ntype, status, text, _key, note, img = m.groups()
            status = STATUS_ALIAS.get(status, status)
            if nid in seen_ids:
                counters[nid[0]] += 1
                nid = nid[0] + str(counters[nid[0]])  # 自動改號,下次儲存即落地
            seen_ids.add(nid)
            if nid[0] != TYPE_PREFIX.get(ntype):
                errors.append({"line": i + 1, "text": f"{nid} 前綴與型別不符(E=效果, X=執行, C=Context)"})
                continue
            if status not in STATUSES:
                errors.append({"line": i + 1, "text": f"{nid} 狀態「{status}」不在 {'/'.join(STATUSES)}"})
                continue
            level = len(indent) // 2
            node = {"id": nid, "type": ntype, "status": status,
                    "text": text.strip(), "note": (note or "").strip(),
                    "note_key": _key or "",  # 保留原欄名:執行節點掛{判準:}不改寫成{產出:}
                    "img": (img or "").strip(), "children": []}
            while stack and stack[-1][0] >= level:
                stack.pop()
            if not stack:
                forest.append(node)
                stack.append((0, node))
            else:
                # 縮排跳級(如 0→2)一律收斂成上一層的直接子節點,寬容手改
                stack[-1][1]["children"].append(node)
                stack.append((stack[-1][0] + 1, node))
        return {"token": self.token(), "file": str(self.path),
                "name": self.path.parent.name, "forest": forest, "errors": errors}

    @staticmethod
    def validate_forest(forest):
        seen = set()

        def walk(nodes):
            if not isinstance(nodes, list):
                raise ValueError("forest 節點必須是 list")
            for nd in nodes:
                nid, ntype = nd.get("id", ""), nd.get("type", "")
                if not ID_RE.match(nid):
                    raise ValueError(f"非法 ID: {nid!r}")
                if nid in seen:
                    raise ValueError(f"重複 ID: {nid}")
                seen.add(nid)
                if ntype not in TYPE_PREFIX or nid[0] != TYPE_PREFIX[ntype]:
                    raise ValueError(f"{nid} 型別/前綴不符")
                if nd.get("status") not in STATUSES:
                    raise ValueError(f"{nid} 非法狀態 {nd.get('status')!r}")
                for one in str(nd.get("img", "") or "").split(","):
                    one = one.strip()  # 多圖=逗號分隔清單,逐一驗檔名
                    if one and (Path(one).name != one or ".." in one):
                        raise ValueError(f"{nid} 非法圖檔名 {one!r}")
                walk(nd.get("children", []))
        walk(forest)

    @staticmethod
    def serialize_forest(forest):
        out = []

        def walk(nodes, depth):
            for nd in nodes:
                text = " ".join(str(nd.get("text", "")).split()) or "(未命名)"
                note = " ".join(str(nd.get("note", "")).split())
                key = nd.get("note_key") or TYPE_NOTEKEY[nd["type"]]
                if key not in TYPE_NOTEKEY.values():
                    key = TYPE_NOTEKEY[nd["type"]]
                ann = f" {{{key}: {note}}}" if note else ""
                img = ", ".join(p.strip() for p in str(nd.get("img", "") or "").split(",") if p.strip())
                ann += f" {{圖: {img}}}" if img else ""
                out.append(f'{"  " * depth}- {nd["id"]}[{nd["type"]}|{nd["status"]}] {text}{ann}')
                walk(nd.get("children", []), depth + 1)
        walk(forest, 0)
        return out

    def save(self, expect_token, forest):
        if self.token() != expect_token:
            return None  # conflict
        # 檔內有無法解析的行時拒寫:整區重寫會把那些行靜默丟掉。前端紅橫幅已鎖編輯,
        # 這裡擋的是直打 API 的 agent/過期 client(v26;v24-25 只有前端單防線)
        bad = self.parse()["errors"]
        if bad:
            raise ValueError(f"檔內有 {len(bad)} 行無法解析(第 {', '.join(str(e['line']) for e in bad)} 行),"
                             "拒絕寫入以免靜默丟行——先修好 md 再存")
        self.validate_forest(forest)
        lines = self.read_lines()
        span = self._marker_span(lines)
        if span is None:
            raise ValueError("檔內沒有 effect-tree 標記,拒絕寫入")
        lines[span[0] + 1:span[1]] = self.serialize_forest(forest)
        self.write_lines(lines)
        return self.parse()


class Handler(BaseHTTPRequestHandler):
    fixed_md = None  # 單檔模式;None=自動掃描

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _projects(self):
        if self.fixed_md:
            p = Path(self.fixed_md)
            return [{"name": p.parent.name, "path": str(p)}]
        return discover_trees()

    def _resolve(self, f):
        """檔案參數對回允許清單,防任意路徑寫檔。"""
        allowed = {str(Path(p["path"]).resolve()): p["path"] for p in self._projects()}
        if not f:
            return Doc(next(iter(allowed.values()))) if allowed else None
        key = str(Path(f).resolve())
        return Doc(allowed[key]) if key in allowed else None

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        f = q.get("f", [None])[0]
        try:
            if url.path in ("/", "/index.html"):
                self._send(200, (HERE / "tree.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/projects":
                self._send(200, {"version": VERSION, "projects": self._projects()})
            elif url.path == "/api/doc":
                doc = self._resolve(f)
                if not doc:
                    return self._send(404, {"error": "no tree file"})
                with IO_LOCK:
                    payload = doc.parse()
                payload["version"] = VERSION
                self._send(200, payload)
            elif url.path == "/api/mtime":
                doc = self._resolve(f)
                if not doc:
                    return self._send(404, {"error": "no tree file"})
                self._send(200, {"token": doc.token(), "version": VERSION})
            elif url.path == "/api/asset":
                doc = self._resolve(f)
                name = q.get("name", [None])[0]
                if not doc or not name or Path(name).name != name:
                    return self._send(400, {"error": "bad asset request"})
                p = doc.path.parent / ASSETS_DIRNAME / name
                ext = p.suffix.lstrip(".").lower()
                if not p.is_file() or ext not in IMG_EXTS:
                    return self._send(404, {"error": "no such asset"})
                self._send(200, p.read_bytes(),
                           f"image/{'jpeg' if ext == 'jpg' else ext}")
            elif url.path == "/api/candidates":
                # 還沒有樹的專案資料夾(供前端下拉「＋建立新樹」);單檔模式不提供
                if self.fixed_md or not PROJECTS_DIR.is_dir():
                    return self._send(200, {"candidates": []})
                have = {p["name"] for p in discover_trees()}
                cands = sorted(d.name for d in PROJECTS_DIR.iterdir()
                               if d.is_dir() and d.name not in have
                               and not d.name.startswith((".", "_")))
                self._send(200, {"candidates": cands})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001 — render/parse 炸掉要回報,不能斷連線裝死
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/upload":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                doc = self._resolve(body.get("file"))
                ext = str(body.get("ext", "png")).lower().lstrip(".")
                if doc is None or ext not in IMG_EXTS:
                    return self._send(400, {"error": "bad upload request"})
                data = base64.b64decode(body["data"])
                if len(data) > 8 * 1024 * 1024:
                    return self._send(400, {"error": "image too large (>8MB)"})
                d = doc.path.parent / ASSETS_DIRNAME
                d.mkdir(exist_ok=True)
                name = time.strftime("paste_%Y%m%d_%H%M%S") + f"_{len(list(d.iterdir()))}.{ext}"
                with IO_LOCK:
                    (d / name).write_bytes(data)
                return self._send(200, {"name": name})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/copyassets":
            # 跨樹剪貼連圖搬家:把來源專案 assets 的圖檔複製到目標專案 assets(同名跳過)
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                src = self._resolve(body.get("src"))
                dst = self._resolve(body.get("dst"))
                if not src or not dst:
                    return self._send(400, {"error": "src/dst not in allowed trees"})
                src_dir = src.path.parent / ASSETS_DIRNAME
                dst_dir = dst.path.parent / ASSETS_DIRNAME
                copied = []
                with IO_LOCK:
                    for name in body.get("names", [])[:200]:
                        name = str(name).strip()
                        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                        if not name or Path(name).name != name or ".." in name or ext not in IMG_EXTS:
                            continue
                        s, d = src_dir / name, dst_dir / name
                        if s.exists():
                            dst_dir.mkdir(exist_ok=True)
                            if not d.exists():
                                d.write_bytes(s.read_bytes())
                            copied.append(name)
                return self._send(200, {"copied": copied})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        if path == "/api/create":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                name = body.get("project", "").strip()
                d = PROJECTS_DIR / name
                # 名稱=單一資料夾名(擋路徑遍歷/隱藏與底線開頭);資料夾不存在則建立(=從 UI 開新專案)
                if self.fixed_md or not name or Path(name).name != name \
                        or name.startswith((".", "_")) or len(name) > 80:
                    return self._send(400, {"error": "invalid project"})
                md = d / TREE_FILENAME
                if md.exists():
                    return self._send(400, {"error": "already exists"})
                with IO_LOCK:
                    d.mkdir(exist_ok=True)
                    bootstrap_file(md)
                return self._send(200, {"path": str(md)})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        if path != "/api/save":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            doc = self._resolve(body.get("file"))
            if doc is None:
                return self._send(400, {"error": "file not in allowed trees"})
            with IO_LOCK:
                result = doc.save(body["expect_token"], body["forest"])
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        if result is None:
            with IO_LOCK:
                fresh = doc.parse()
            return self._send(409, {"error": "conflict", "doc": fresh})
        result["version"] = VERSION
        self._send(200, result)

    def log_message(self, fmt, *args):
        if getattr(Handler, "debug", False):
            print(f"[req] {fmt % args}", flush=True)


def bootstrap_file(md: Path):
    """單檔模式下檔案不存在→照 TEMPLATE 建立。"""
    tpl = (HERE / "TEMPLATE.md").read_text(encoding="utf-8")
    name = md.parent.name
    md.write_text(tpl.replace("<專案名>", name), encoding="utf-8")
    print(f"已照 TEMPLATE 建立 {md}")


def main():
    global PROJECTS_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("md", nargs="?", default=None,
                    help="指定單一效果樹;不帶=自動掃描 projects/*/_效果樹.md")
    ap.add_argument("--port", type=int, default=8778)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--projects-dir", default=None,
                    help="自動掃描的專案根目錄(預設=本 repo 的 1_Projects);"
                         "寫入白名單與 /api/create 一併錨到此目錄")
    args = ap.parse_args()
    Handler.debug = args.debug
    if args.projects_dir:
        p = Path(args.projects_dir).resolve()
        if not p.is_dir():
            sys.exit(f"--projects-dir 不存在或不是資料夾: {p}")
        PROJECTS_DIR = p
    if args.md:
        md = Path(args.md)
        if not md.exists():
            bootstrap_file(md)
        Handler.fixed_md = str(md)
        scope = f"單檔: {md}"
    else:
        trees = discover_trees()
        if not trees:
            sys.exit(f"在 {PROJECTS_DIR} 底下找不到任何 {TREE_FILENAME};先照 TEMPLATE.md 建一份,"
                     f"或用 py tree.py <路徑> 讓工具自動建立")
        scope = f"自動掃描到 {len(trees)} 棵樹(預設開最近改動: {trees[0]['name']})"
    url = f"http://127.0.0.1:{args.port}/"
    # 冪等啟動:port 上已有活的自己就直接開瀏覽器退出(Windows SO_REUSEADDR 允許雙綁,不能靠 bind 失敗判斷)
    try:
        with urllib.request.urlopen(url + "api/projects", timeout=1.5) as r:
            if r.status == 200:
                print(f"效果樹已在跑 → 直接開 {url}")
                if not args.no_browser:
                    webbrowser.open(url)
                return
    except Exception:
        pass
    ThreadingHTTPServer.allow_reuse_address = False
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print(f"效果樹 → {url}  ({scope})")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
