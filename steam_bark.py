#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
steam-bark —— 指定 Steam 好友上线 / 开游戏 / 换游戏 / 下线 → Bark 推送

特点：
  * 只监控 WATCH 白名单里的人，不碰你的全部好友
  * 零第三方依赖（只用标准库）
  * 不需要登录 Steam、不需要挂客户端，只调官方 Web API
  * 镜像内不含任何密钥：所有配置都靠运行时环境变量注入

用法：
    python steam_bark.py                   # 常驻监控（WATCH 必填）
    python steam_bark.py --once            # 只查一轮、打印状态表，不推送
    python steam_bark.py --list-friends    # 打印你的好友列表（含 Steam64 ID），用于挑选 WATCH
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.steampowered.com"
STATE_VERSION = 2


# ---------- 配置 ----------
def env(name, default=""):
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


def env_bool(name, default=False):
    return env(name, str(default)).lower() in ("1", "true", "yes", "y", "on")


def env_int(name, default):
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def parse_watch(raw):
    """WATCH=76561198000000001=小明,76561198000000002

    支持用 = 追加显示备注；不写备注就用对方当前的 Steam 昵称。
    """
    seen, out = set(), []
    for item in raw.replace("\n", ",").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            sid, alias = item.split("=", 1)
            sid, alias = sid.strip(), alias.strip()
        else:
            sid, alias = item, ""
        if sid and sid not in seen:
            seen.add(sid)
            out.append((sid, alias))
    return out


CFG = {
    "api_key": env("STEAM_API_KEY"),
    "watch": parse_watch(env("WATCH")),
    "list_steam_id": env("LIST_STEAM_ID"),
    "bark_server": env("BARK_SERVER", env("BARK_URL", "http://192.168.100.200:8880")).rstrip("/"),
    "bark_key": env("BARK_KEY"),
    "bark_group": env("BARK_GROUP", "Steam"),
    "bark_sound": env("BARK_SOUND", "calypso"),
    "bark_level": env("BARK_LEVEL", "active"),
    "poll": max(20, env_int("POLL_SECONDS", 60)),
    "only_game": env_bool("ONLY_GAME", False),
    "notify_offline": env_bool("NOTIFY_OFFLINE", True),
    "notify_switch": env_bool("NOTIFY_SWITCH", False),
    "alert_on_error": env_bool("ALERT_ON_ERROR", True),
    "alert_after_fails": max(1, env_int("ALERT_AFTER_FAILS", 3)),
    "quiet": env("QUIET_HOURS"),
    "state_file": env("STATE_FILE", "/data/state.json"),
    "tz_offset": env("TZ_OFFSET_HOURS", "8"),
}

ALIAS = {sid: alias for sid, alias in CFG["watch"] if alias}

try:
    TZ = timezone(timedelta(hours=float(CFG["tz_offset"])))
except ValueError:
    TZ = timezone(timedelta(hours=8))


def log(msg):
    print(f"[{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------- 静默时段 ----------
def in_quiet(now):
    q = CFG["quiet"]
    if not q:
        return False
    m = re.match(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$", q)
    if not m:
        return False
    sh, sm, eh, em = (int(x) for x in m.groups())
    cur, start, end = now.hour * 60 + now.minute, sh * 60 + sm, eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end  # 跨午夜


# ---------- Steam API ----------
def api_get(path, params):
    params = dict(params)
    params["key"] = CFG["api_key"]
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "steam-bark/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_summaries(ids):
    players = []
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        data = api_get(
            "ISteamUser/GetPlayerSummaries/v2/", {"steamids": ",".join(chunk)}
        )
        players.extend(((data.get("response") or {}).get("players")) or [])
    return players


def fetch_friend_list(steam_id):
    data = api_get(
        "ISteamUser/GetFriendList/v1/",
        {"steamid": steam_id, "relationship": "friend"},
    )
    entries = ((data.get("friendslist") or {}).get("friends")) or []
    return [e.get("steamid") for e in entries if e.get("steamid")]


# ---------- 状态判定 ----------
PS_MAP = {1: "online", 2: "busy", 3: "away", 4: "snooze", 5: "online", 6: "online"}


def classify(player):
    """返回 (state, game)。state ∈ offline / online / ingame"""
    if int(player.get("personastate", 0) or 0) == 0:
        return "offline", None
    game = (player.get("gameextrainfo") or "").strip()
    if game:
        return "ingame", game
    return PS_MAP.get(int(player.get("personastate", 1)), "online"), None


def display_name(player):
    sid = str(player.get("steamid", ""))
    return ALIAS.get(sid) or player.get("personaname") or sid


def decide(prev_state, prev_game, state, game, name):
    """状态跃迁 → (标题, 正文)；None 表示不推送"""
    if state == "offline" and prev_state != "offline":
        if not CFG["notify_offline"]:
            return None
        return "⚫ Steam 好友下线", f"{name} 已下线"

    if state == "ingame":
        if prev_state == "ingame":
            if not CFG["notify_switch"] or prev_game == game:
                return None
            return "🎮 Steam 好友换游戏", f"{name} 换成《{game}》"
        return "🎮 Steam 好友开游戏", f"{name} 开始玩《{game}》"

    if state == "online":
        if prev_state == "offline" and not CFG["only_game"]:
            return "🟢 Steam 好友上线", f"{name} 已上线"
        return None  # 大厅↔离开/忙碌 之间来回切，不播报

    return None


# ---------- Bark ----------
def _bark_post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def bark(title, body):
    """主路径 POST /{key}（iOS App 文档写法）；服务端不接受时退回 POST /push + device_key。

    与 ts3-bark 的策略保持一致，兼容不同版本的 bark-server。
    """
    payload = {
        "title": title,
        "body": body,
        "group": CFG["bark_group"],
        "sound": CFG["bark_sound"],
        "level": CFG["bark_level"],
    }
    first_err = None
    try:
        _bark_post(f"{CFG['bark_server']}/{CFG['bark_key']}", payload)
        log(f"推送 → {title} | {body}")
        return
    except Exception as exc:  # noqa: BLE001
        first_err = exc

    payload2 = dict(payload)
    payload2["device_key"] = CFG["bark_key"]
    try:
        _bark_post(f"{CFG['bark_server']}/push", payload2)
        log(f"推送 → {title} | {body}（/push 回退）")
    except Exception as exc:  # noqa: BLE001
        log(f"推送失败：{first_err} / {exc}")


# ---------- 状态持久化 ----------
def load_state():
    try:
        with open(CFG["state_file"], "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data.get("players") or {}
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        log(f"读取状态失败，按首次运行处理：{exc}")
    return {}


def save_state(players):
    path = CFG["state_file"]
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {"version": STATE_VERSION, "updated": int(time.time()), "players": players},
                f,
                ensure_ascii=False,
                indent=1,
            )
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        log(f"写入状态失败：{exc}")


# ---------- 子命令 ----------
def cmd_list_friends():
    if not CFG["api_key"]:
        log("缺少 STEAM_API_KEY")
        return 2
    if not CFG["list_steam_id"]:
        log("缺少 LIST_STEAM_ID（你自己的 Steam64 ID），无法拉取好友列表")
        return 2

    ids = fetch_friend_list(CFG["list_steam_id"])
    if not ids:
        log("好友列表为空：请把 Steam 隐私设置里的『好友列表』设为公开")
        return 3

    players = {str(p.get("steamid")): p for p in fetch_summaries(ids)}
    tag = {"offline": "○ 离线", "online": "● 在线", "ingame": "● 游戏中"}
    print(f"\n共 {len(players)} 位好友（挑你要关注的，把 ID 填进 WATCH）：\n")
    for p in sorted(players.values(), key=lambda x: (x.get("personaname") or "").lower()):
        state, game = classify(p)
        tail = f"  《{game}》" if game else ""
        print(f"  {tag[state]}  {p.get('steamid')}  {p.get('personaname')}{tail}")
    print("\n示例：WATCH=76561198000000001=小明,76561198000000002\n")
    return 0


def cmd_once():
    if not CFG["api_key"]:
        log("缺少 STEAM_API_KEY")
        return 2
    if not CFG["watch"]:
        log("WATCH 为空：请先指定要关注的好友（可用 --list-friends 查 ID）")
        return 2

    players = fetch_summaries([sid for sid, _ in CFG["watch"]])
    tag = {"offline": "○ 离线", "online": "● 在线", "ingame": "● 游戏中"}
    print(f"\n观察名单 {len(CFG['watch'])} 人：\n")
    found = set()
    for p in players:
        sid = str(p.get("steamid"))
        found.add(sid)
        state, game = classify(p)
        tail = f"  《{game}》" if game else ""
        note = ""
        if state == "offline" and int(p.get("communityvisibilitystate", 3) or 3) != 3:
            note = "  (资料仅好友可见/私密，在线状态拿不到)"
        print(f"  {tag[state]}  {display_name(p)}{tail}{note}")
    for sid, alias in CFG["watch"]:
        if sid not in found:
            print(f"  ⚠ 查不到  {alias or sid}（ID 写错了？）")
    print()
    return 0


# ---------- 主循环 ----------
def main():
    missing = [k for k in ("api_key", "bark_key") if not CFG[k]]
    if missing:
        log("缺少必填配置：" + ", ".join(m.upper() for m in missing))
        return 2
    if not CFG["watch"]:
        log("WATCH 为空：请指定要关注的好友 Steam64 ID（可用 --list-friends 查看）")
        return 2

    log(
        "启动 | 关注 {n} 人 | 轮询 {poll}s | group={group} | only_game={og} | 下线通知={off} | 静默={q}".format(
            n=len(CFG["watch"]),
            poll=CFG["poll"],
            group=CFG["bark_group"],
            og=CFG["only_game"],
            off=CFG["notify_offline"],
            q=CFG["quiet"] or "关闭",
        )
    )
    log("名单：" + "、".join(alias or sid for sid, alias in CFG["watch"]))

    ids = [sid for sid, _ in CFG["watch"]]
    players = load_state()
    baseline = not players  # 首次运行只建基线，不推送
    if baseline:
        log("未找到历史状态：本轮只建立基线，不推送")

    error_streak, rounds, last_online, alerted = 0, 0, None, False

    while True:
        try:
            now = time.time()
            summaries = fetch_summaries(ids)
            notifications, changed = [], False

            for p in summaries:
                sid = str(p.get("steamid", ""))
                if not sid:
                    continue
                name = display_name(p)
                state, game = classify(p)
                prev = players.get(sid)

                if prev is None:
                    players[sid] = {"name": name, "state": state, "game": game, "ts": int(now)}
                    changed = True
                    continue

                prev_state, prev_game = prev.get("state"), prev.get("game")
                if prev_state == state and prev_game == game:
                    if prev.get("name") != name:
                        prev["name"] = name
                        changed = True
                    continue

                prev["name"] = name
                prev["state"] = state
                prev["game"] = game
                prev["ts"] = int(now)
                changed = True

                if baseline:
                    continue
                hit = decide(prev_state, prev_game, state, game, name)
                if hit:
                    notifications.append(hit)

            baseline = False
            if changed:
                save_state(players)

            if notifications:
                if in_quiet(datetime.now(TZ)):
                    log("静默时段，丢弃 {n} 条：{t}".format(
                        n=len(notifications), t=" / ".join(n[0] for n in notifications)))
                else:
                    for title, body in notifications:
                        bark(title, body)

            error_streak = 0
            if alerted:
                bark("✅ Steam 监控恢复", "查询已恢复正常")
                alerted = False
            rounds += 1
            online = sum(1 for v in players.values() if v.get("state") != "offline")
            if online != last_online or rounds % 30 == 0:
                log(f"心跳 | 关注 {len(ids)} 人，在线 {online} 人")
                last_online = online

        except Exception as exc:  # noqa: BLE001
            error_streak += 1
            if isinstance(exc, urllib.error.HTTPError):
                detail = ""
                try:
                    detail = " " + exc.read().decode("utf-8", "replace")[:200]
                except Exception:  # noqa: BLE001
                    pass
                log(f"HTTP {exc.code}（{exc.reason}）{detail}")
            else:
                log(f"轮询失败：{exc}")
            if (
                CFG["alert_on_error"]
                and not alerted
                and error_streak >= CFG["alert_after_fails"]
            ):
                bark("⛔ Steam 监控中断", f"连续 {error_streak} 次查询失败")
                alerted = True

        delay = CFG["poll"]
        if error_streak:
            delay = min(300, CFG["poll"] * (2 ** min(error_streak, 3)))
            log(f"第 {error_streak} 次失败，{delay}s 后重试")
        time.sleep(delay)


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        if "--list-friends" in args:
            sys.exit(cmd_list_friends())
        if "--once" in args:
            sys.exit(cmd_once())
        sys.exit(main())
    except KeyboardInterrupt:
        log("已停止")
