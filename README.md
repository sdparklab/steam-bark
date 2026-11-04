# steam-bark

![Docker Pulls](https://img.shields.io/docker/pulls/sdparklab/steam-bark)
![Docker Image Size](https://img.shields.io/docker/image-size/sdparklab/steam-bark)
![GitHub stars](https://img.shields.io/github/stars/sdparklab/steam-bark?style=flat)
![License](https://img.shields.io/badge/license-MIT-blue)

将指定 Steam 好友的上线、开游戏、换游戏、下线推送至 Bark（iOS）。

- 镜像：`docker pull sdparklab/steam-bark`（[Docker Hub](https://hub.docker.com/r/sdparklab/steam-bark)）
- 源码：[github.com/sdparklab/steam-bark](https://github.com/sdparklab/steam-bark)

<!--
<p align="center">
  <img src="screenshot.png" alt="推送示例" width="360">
</p>
-->

## 简介

下班后想找人开黑，但朋友们的上线时间不固定，也没人会主动通知谁。一直盯着好友列表不现实，而 Steam 虽然提供官方 Web API，却没有「好友状态变化推给我」的机制——只能自己主动查询。

本项目定时查询指定好友的状态，仅在状态真正跃迁时推送：上线、开始游戏、下线。不需要登录 Steam，不需要保持客户端运行，一个容器常驻即可。

## 特性

- 只监控 `WATCH` 白名单内的好友，不读取全部好友列表
- 不需要登录 Steam，不需要运行 Steam 客户端，仅调用官方 Web API
- 状态机去重：仅在离线↔在线、进入游戏、下线时推送；在线↔离开↔忙碌之间切换不推送
- 首次启动仅建立基线，不会把当前在线的好友重复播报一遍
- 状态持久化至 `/data`，容器重启后继续比对，不会重发
- 支持只播报「开始玩游戏」模式（`ONLY_GAME`），忽略单纯打开客户端
- 支持静默时段（`QUIET_HOURS`），跨午夜有效
- 查询连续失败达 `ALERT_AFTER_FAILS` 次时推送告警，恢复后推送恢复通知
- 仅依赖 Python 标准库，镜像约 55 MB
- `GetPlayerSummaries` 单次可查询 100 个 SteamID，默认配置下日配额用量约 1.5%

## 工作方式

```
Steam Web API（HTTPS）
      │ GET ISteamUser/GetPlayerSummaries（每 POLL_SECONDS 秒）
      ▼
   本容器（常驻）
      │ HTTP POST
      ▼
   Bark（官方 api.day.app 或自建 bark-server）→ iPhone
```

与事件订阅型的播报工具不同，Steam 未提供好友状态的推送接口，因此本项目采用轮询。推送延迟最大为 `POLL_SECONDS`，平均约一半。

## 快速开始

```bash
docker run -d --name steam-bark --restart=unless-stopped \
  -v /mnt/user/appdata/steam-bark/data:/data \
  -e STEAM_API_KEY=你的Steam Web API Key \
  -e WATCH="76561198000000001=小明,76561198000000002" \
  -e BARK_SERVER=https://api.day.app \
  -e BARK_KEY=你的Bark设备key \
  sdparklab/steam-bark:latest
```

日志出现以下内容表示运行正常：

```
启动 | 关注 2 人 | 轮询 60s | group=Steam | only_game=False | 下线通知=True | 静默=关闭
名单：小明、76561198000000002
未找到历史状态：本轮只建立基线，不推送
心跳 | 关注 2 人，在线 0 人
```

此后有人上线或开始游戏时：

```
推送 → 🟢 Steam 好友上线 | 小明 已上线
心跳 | 关注 2 人，在线 1 人
```

### 验证模式

`--once` 仅查询一次并打印关注名单的当前状态，不发送推送：

```bash
docker run --rm \
  -e STEAM_API_KEY=你的key \
  -e WATCH="76561198000000001=小明" \
  sdparklab/steam-bark:latest python /app/steam_bark.py --once
```

`--list-friends` 打印你的全部好友及 Steam64 ID，用于挑选 `WATCH`：

```bash
docker run --rm \
  -e STEAM_API_KEY=你的key \
  -e LIST_STEAM_ID=你自己的Steam64ID \
  sdparklab/steam-bark:latest python /app/steam_bark.py --list-friends
```

### Docker Compose

```yaml
services:
  steam-bark:
    image: sdparklab/steam-bark:latest
    container_name: steam-bark
    restart: unless-stopped
    environment:
      TZ: Asia/Shanghai
      STEAM_API_KEY: 你的Steam Web API Key
      WATCH: "76561198000000001=小明,76561198000000002"
      BARK_SERVER: https://api.day.app
      BARK_KEY: 你的Bark设备key
    volumes:
      - ./data:/data
```

### Unraid

Docker 页签 → ADD CONTAINER：

| 配置项 | 值 |
|---|---|
| Name | `steam-bark` |
| Repository | `sdparklab/steam-bark:latest` |
| Network Type | `Bridge` |
| Autostart | `On` |
| Path | 容器 `/data` ← 主机 `/mnt/user/appdata/steam-bark/data` |

其余环境变量通过 Add another Path, Port, Variable, Label or Device 添加（类型选择 Variable）。无需映射端口。

`/data` 用于保存状态文件。不映射不会导致重复推送（首次启动仍是基线），但容器重建后历史状态会丢失。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `STEAM_API_KEY` | 必填 | Steam Web API Key |
| `WATCH` | 必填 | 关注名单，`steamid` 或 `steamid=备注`，逗号分隔 |
| `BARK_KEY` | 必填 | Bark 设备 key（22 位 device_key，非 64 位 Device Token） |
| `BARK_SERVER` | `http://192.168.100.200:8880` | Bark 服务地址 |
| `BARK_GROUP` | `Steam` | 通知分组 |
| `BARK_SOUND` | `calypso` | 通知铃声 |
| `BARK_LEVEL` | `active` | 推送级别 |
| `POLL_SECONDS` | `60` | 轮询间隔（秒），最小 20 |
| `ONLY_GAME` | `false` | 仅在开始玩游戏时推送，忽略单纯上线 |
| `NOTIFY_OFFLINE` | `true` | 是否推送下线事件 |
| `NOTIFY_SWITCH` | `false` | 是否推送换游戏事件 |
| `QUIET_HOURS` | 空 | 静默时段，如 `02:00-10:00`，支持跨午夜 |
| `ALERT_ON_ERROR` | `true` | 是否推送查询异常告警 |
| `ALERT_AFTER_FAILS` | `3` | 触发告警的连续失败次数 |
| `LIST_STEAM_ID` | 空 | 仅 `--list-friends` 使用 |
| `STATE_FILE` | `/data/state.json` | 状态文件路径 |
| `TZ_OFFSET_HOURS` | `8` | 日志时间的时区偏移 |

全部参数通过环境变量配置，无需修改代码。

## 推送格式

| 事件 | 标题 | 正文 |
|---|---|---|
| 上线 | 🟢 Steam 好友上线 | `小明 已上线` |
| 开始游戏 | 🎮 Steam 好友开游戏 | `小明 开始玩《艾尔登法环》` |
| 换游戏 | 🎮 Steam 好友换游戏 | `小明 换成《CS2》` |
| 下线 | ⚫ Steam 好友下线 | `小明 已下线` |
| 查询失败 | ⛔ Steam 监控中断 | `连续 3 次查询失败` |
| 查询恢复 | ✅ Steam 监控恢复 | `查询已恢复正常` |

## 准备工作

### 1. Steam Web API Key

打开 <https://steamcommunity.com/dev/apikey>，域名随意填写（`localhost` 即可），获取 32 位 Key。该 Key 只能读取公开数据，不具备账号操作权限。

### 2. Steam64 ID

- 个人主页为 `steamcommunity.com/profiles/7656119xxxxxxxxxx` → 数字部分即 Steam64 ID
- 个人主页为 `steamcommunity.com/id/自定义名` → 用 <https://steamid.io> 转换

自己的 ID 仅 `--list-friends` 需要；关注名单里的 ID 可来自好友的主页链接或 `--list-friends` 输出。

### 3. Bark 设备 key

Bark App 中设备的 `device_key`，形如 `iqV9Ky65JNoVoW8zoPHo5B`。

> 「设置 → 信息」页里的 `Device Token`（64 位十六进制）是 APNs 令牌，不能作为 `device_key` 使用，误用会返回 `400`。

## 隐私设置

被关注者必须将 Steam 隐私中的「游戏详情」设为公开。资料为私密、仅好友可见，或处于隐身状态时，`personastate` 恒为 `0`，表现为始终离线。这是 Steam 的隐私设计，无法绕过。

非好友同样可以监控，前提是对方资料公开。

## 常见问题

**某位好友始终显示离线**

对方的「游戏详情」不是公开状态，或处于隐身。可用 `--once` 核对，输出中这些条目会标注「资料仅好友可见/私密」。

**调用返回 400 且响应体为空**

`BARK_KEY` 填成了 64 位的 Device Token。应使用 App 中的 22 位 device_key。

**收不到推送**

先确认 Bark 侧可用：

```bash
curl -X POST 'http://<Bark 地址>/<device_key>' -H 'Content-Type: application/json' \
  -d '{"title":"测试","body":"hello","group":"Steam"}'
```

返回 `{"code":200,"message":"success"}` 但手机无提示时，问题在 App 的通知权限或静音设置。日志中出现 `推送失败：` 时，则说明容器无法访问 `BARK_SERVER`。

**日志出现「/push 回退」**

正常现象。程序优先使用 `POST /{key}`，服务端不支持该端点时自动改用 `POST /push`（device_key 放入 JSON），兼容不同版本的 bark-server。

**为什么延迟比 TeamSpeak 播报大**

TS3 采用 ServerQuery 事件订阅，事件发生即推送；Steam 未提供同类接口，只能轮询，延迟最大为 `POLL_SECONDS`。将 `POLL_SECONDS` 调至 20 可缩短延迟，配额仍远未触及上限（约 4300 次/天，上限 100000 次/天）。

**好友改名后通知里显示的还是旧名字**

`WATCH` 中的备注会覆盖实时昵称，这是有意设计——昵称可随时修改，备注用于长期识别。去掉 `=备注` 即可显示实时昵称。

**换游戏太吵**

`NOTIFY_SWITCH` 默认为 `false`。只想在真正开打时收到通知，可将 `ONLY_GAME` 设为 `true`。

## 本地构建

```bash
git clone https://github.com/sdparklab/steam-bark.git
cd steam-bark
docker build -t steam-bark:dev .
```

主程序为单文件 `steam_bark.py`，仅使用 Python 标准库，无第三方依赖。

镜像中不包含任何密钥，全部配置在运行时通过环境变量注入。`.env` 已被 `.dockerignore` 与 `.gitignore` 排除。

## 许可证

MIT
