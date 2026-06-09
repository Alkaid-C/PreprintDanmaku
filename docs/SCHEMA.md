# DanmakuHime · SSE Schema

在本项目中，后端通过 SSE 推送弹幕事件流给前端。本文件是**后端→前端的字段契约**，只规定字段与语义。

**文档API_VERSION**: `0.4`「回忆是抓不到的月光」
**文档发布日期**：Jun 09, 2026

后端的API版本 由`main.py` 的 `API_VERSION` 常量指定；前端的API版本由 `index.html` 顶部注释里的 `api_version`指定。后端只服务 `api_version` 与之**精确相等**的前端。

---

## 0. 传输

- 单条 SSE `data:` 负载 = 一个 **JSON 对象**(一个事件)。
- 事件按发生顺序推送,`id` 单调递增。
- 编码 UTF-8。

```
data: {"type":"danmaku","id":1024,"timestamp":"21:03","sender":{...},"text":"这个证明太优雅了"}
```

---

## 1. 公共字段(所有事件)

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | string | `init` \| `danmaku` \| `gift` \| `superchat` \| `guard` |
| `id` | int | **单调递增**序列号 |
| `timestamp` | string | 事件时间,`HH:MM` |

> `init` 没有 `sender`;其余四类都带 `sender`。

---

## 2. `sender`(身份字典)

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | string | 用户 uid(字符串化);系统消息为 `"0"` |
| `username` | string | 用户名 |
| `avatar_url` | string | 头像链接,如 `https://i0.hdslb.com/bfs/face/xxx.jpg`;**`GUARD_BUY` / 系统消息为空串 `""`**(事件不提供) |
| `badge_name` | string | 粉丝牌名;**空串 `""` = 无牌** |
| `badge_level` | int | 粉丝牌等级 |
| `guard_level` | int | 用户当前大航海身份 `0/1/2/3` = 无 / 舰长 / 提督 / 总督 |

---

## 3. 各事件类型

### 3.1 `danmaku` — 普通弹幕

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sender` | dict | ✓ | 见上 |
| `text` | string | ✓ | 弹幕正文(通常 < 40 字符) |
| `is_image` | bool | ✓ | 是否为图片弹幕 |
| `image_url` | string | — | 图片地址(仅 `is_image=true` 时可能出现) |

> `text` 在任何情况下都保证存在且有意义(`is_image=true` 时为图片 caption)。

### 3.2 `gift` — 礼物

| 字段 | 类型 | 说明 |
|---|---|---|
| `sender` | dict | 见上 |
| `gift_name` | string | 礼物名 |
| `gift_count` | int | 数量(连击数),≥ 1 |
| `value_cents` | int | 总价值(分);免费礼物(银瓜子)为 `0` |

### 3.3 `superchat` — SuperChat

| 字段 | 类型 | 说明 |
|---|---|---|
| `sender` | dict | 见上 |
| `dwell_seconds` | int | B站下发的置顶展示时长(秒) |
| `value_cents` | int | 金额(分) |
| `text` | string | 留言正文 |

> 后端自身的系统通知(重连提示、直播结束统计、转发的错误)也以 `superchat` 形态推送,其 `sender.uid` 为 `"0"`、`value_cents` 为 `0`,前端可据此识别。

### 3.4 `guard` — 上船(大航海)

| 字段 | 类型 | 说明 |
|---|---|---|
| `sender` | dict | 见上 |
| `guard_level` | int | `1` / `2` / `3` = 舰长 / 提督 / 总督(本次开通的档位) |
| `months` | int | 开通的月数 |

### 3.5 `init` — 初始化

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `room_info` | dict | ✓ | 直播间中立事实;见下表 |

`room_info`:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `room_id` | int | ✓ | 配置文件指定的直播间号 |
| `title` | string | ✓ | 直播间标题 |
| `streamer_username` | string | ✓ | 主播用户名 |
| `streamer_uid` | int | ✓ | 主播 uid |
| `streamer_avatar_url` | string | ✓ | 主播头像 URL |
| `parent_area_name` | string | ✓ | 父分区名 |
| `area_name` | string | ✓ | 子分区名 |
| `cover_image_url` | string | ✓ | 直播间封面 URL |

> 除 `room_id` 来自后端配置外,其余字段由B站API获取。如果该接口连续失败,后端仍会发送同形状的 `room_info` 并继续启动:字符串字段为空串,`streamer_uid` 为 `0`。

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

---

## 5. 近期改动(Recent Changes)

### `0.3->0.4`

**移除字段**

- `guard.dwell_seconds`:guard 事件没有b站下发的展示时长，原值是后端按等级合成的。展示时长由前端决定。
- `superchat.level`:后端不再区分 SC 档位；如需按金额分档，前端依 `value_cents` 自行判断。

**金额字段统一为 `value_cents`(单位「分」写进名字)**
- `gift.gifttotalvalue` → `value_cents`
- `superchat.value` → `value_cents`

**字段名统一为 snake_case**
- `sender.badgename` → `badge_name`
- `sender.badgelevel` → `badge_level`
- `sender.guardstat` → `guard_level`(语义不变:用户当前大航海身份 0/1/2/3)
- `gift.giftname` → `gift_name`
- `gift.giftcount` → `gift_count`
- `guard.level` → `guard_level`(与 `sender.guard_level` 同名;在 `guard` 事件里二者取值相同)
- `room_info.streamer_uname` → `streamer_username`(与 `sender.username` 对齐)
