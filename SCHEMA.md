# DanmakuHime · SSE Schema

后端通过 SSE 推送一条事件流。本文件是**后端→前端的字段契约**,只规定字段与语义,不规定前端如何渲染。

> **契约版本(API_VERSION):`0.3`(代号「回忆是抓不到的月光」)** —— 本契约的版本号(代号仅供展示、不参与校验)。真相源是 `main.py` 的 `API_VERSION` 常量与各前端 `index.html` 顶部注释里的 `api_version`(本文档不随发布包分发,仅作人读说明)。后端只服务 `api_version` 与之**精确相等**的前端。**改动本文件中的字段/语义时,请同步提升 `API_VERSION`。**

---

## 0. 传输

- 单条 SSE `data:` 负载 = 一个 **JSON 对象**(一个事件)。
- 事件按发生顺序推送,`id` 单调递增(可据此排序 / 去重 / 补帧)。
- 编码 UTF-8。

```
data: {"type":"danmaku","id":1024,"timestamp":"21:03","sender":{...},"text":"这个证明太优雅了"}
```

---

## 1. 公共字段(所有事件)

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | string | `init` \| `danmaku` \| `gift` \| `superchat` \| `guard` |
| `id` | int | **单调递增**序列号,用于排序/去重/补帧 |
| `timestamp` | string | 事件时间,`HH:MM` |

> `init` 没有 `sender`;其余四类都带 `sender`。

---

## 2. `sender`(身份字典)

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | string | 用户 uid(字符串化);系统消息为 `"0"`,`GUARD_BUY` 取扁平 `uid` |
| `username` | string | 用户名(可打码,如 `迟***`) |
| `avatar_url` | string | 头像链接,如 `https://i0.hdslb.com/bfs/face/xxx.jpg`;**`GUARD_BUY` / 系统消息为空串 `""`**(事件不提供) |
| `badgename` | string | 粉丝牌名;**空串 `""` = 无牌** |
| `badgelevel` | int | 粉丝牌等级 |
| `guardstat` | int | 大航海身份 `0/1/2/3` = 无 / 舰长 / 提督 / 总督 |

---

## 3. 各事件类型

### 3.1 `danmaku` — 普通弹幕

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sender` | dict | ✓ | 见上 |
| `text` | string | ✓ | 弹幕正文(通常 < 40 字符);**必定存在且有意义** —— 当 `is_image=true` 时,此字段是图片的 caption |
| `is_image` | bool | ✓ | 是否为图片弹幕 |
| `image_url` | string | — | 图片地址(仅 `is_image=true` 时可能出现) |

> `text` 在任何情况下都保证存在且有意义(`is_image=true` 时为图片 caption)。

### 3.2 `gift` — 礼物

| 字段 | 类型 | 说明 |
|---|---|---|
| `sender` | dict | 见上 |
| `giftname` | string | 礼物名 |
| `giftcount` | int | 数量(连击数),≥ 1 |
| `gifttotalvalue` | int | 总价值(**分**) |

> 礼物不带 `dwell_seconds`:后端不指定展示时长。

### 3.3 `superchat` — SuperChat

| 字段 | 类型 | 说明 |
|---|---|---|
| `sender` | dict | 见上 |
| `level` | int | SC 档位 `1` / `2` |
| `dwell_seconds` | int | 展示时长(秒),后端权威值 |
| `value` | int | 金额(**分**) |
| `text` | string | 留言正文 |

### 3.4 `guard` — 上船(大航海)

| 字段 | 类型 | 说明 |
|---|---|---|
| `sender` | dict | 见上 |
| `level` | int | `1` / `2` / `3` = 舰长 / 提督 / 总督 |
| `months` | int | 开通的月数 |
| `dwell_seconds` | int | 展示时长(秒),后端权威值 |

### 3.5 `init` — 初始化

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `room_info` | dict | ✓ | 直播间中立事实;见下表 |

`room_info`:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `room_id` | int | ✓ | 配置文件指定的直播间号 |
| `title` | string | ✓ | 直播间标题,来自 `LiveRoom.get_room_info().room_info.title` |
| `streamer_uname` | string | ✓ | 主播用户名,来自 `anchor_info.base_info.uname` |
| `streamer_uid` | int | ✓ | 主播 uid,来自 `room_info.uid` |
| `streamer_avatar_url` | string | ✓ | 主播头像 URL,来自 `anchor_info.base_info.face` |
| `parent_area_name` | string | ✓ | 父分区名,来自 `room_info.parent_area_name` |
| `area_name` | string | ✓ | 子分区名,来自 `room_info.area_name` |
| `cover_image_url` | string | ✓ | 直播间封面 URL,来自 `room_info.cover` |

> `init` 只承载中立事实。除 `room_id` 来自后端配置外,其余字段来自 `LiveRoom.get_room_info()`。如果该公开接口连续失败,后端仍会发送同形状的 `room_info` 并继续启动:字符串字段为空串,`streamer_uid` 为 `0`。标题、作者、分类、主题等前端解释配置不属于本契约,由具体前端自行加载。

---

## 4. 接入说明

前端 `useDanmakuStream` 只连接后端 SSE:

```js
const es = new EventSource('/stream');
es.onmessage = (m) => {
  const ev = JSON.parse(m.data);
  if (ev.type === 'init') applyInit(ev);   // 读取 ev.room_info
  else emit(adapt(ev));                     // adapt(): 后端字段 → 内部事件形状
};
```

- `adapt()` 负责字段改名/缺省填充。
- 没有离线模拟器;需要通过后端服务访问页面。
