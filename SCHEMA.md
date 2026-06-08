# 弹幕预印本 · SSE Schema

后端通过 SSE 推送一条事件流。本文件是**后端→前端的字段契约**,只规定字段与语义,不规定前端如何渲染。

> **契约版本(API_VERSION):`0.2`** —— 本契约的版本号。真相源是 `app.py` 的 `API_VERSION` 常量与各前端 `index.html` 顶部注释里的 `api_version`(本文档不随发布包分发,仅作人读说明)。后端只服务 `api_version` 与之**精确相等**的前端。**改动本文件中的字段/语义时,请同步提升 `API_VERSION`。**

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
| `stamp_label` | string | — | 来源标签,如 `Bilibili` |
| `preprint_id` | string | — | 条目标识,如 `1921712061` |
| `category` | string | — | 分类,如 `虚拟.日常` |
| `authors` | array | — | 作者列表;每项可含 `name` / `affiliation` / `corresponding` |
| `anchor` | string | — | 兼容旧字段: 单行作者名 |
| `room_title` | string | — | 直播间标题 |

> 报头字段无默认值,后端需通过 `init` 全部下发。

---

## 4. 接入说明

前端 `useDanmakuStream` 只连接后端 SSE:

```js
const es = new EventSource('/stream');
es.onmessage = (m) => {
  const ev = JSON.parse(m.data);
  if (ev.type === 'init') applyInit(ev);   // 填充 DM_META
  else emit(adapt(ev));                     // adapt(): 后端字段 → 内部事件形状
};
```

- `adapt()` 负责字段改名/缺省填充。
- 没有离线模拟器;需要通过后端服务访问页面。
