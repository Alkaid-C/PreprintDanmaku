# Bilibili 直播事件数据格式参考

`bilibili_api` 的 `live.LiveDanmaku(...).on("ALL")` 回调收到的事件格式说明。
样本数据见 `Record.txt`（房间 `1921712061` 一整场直播的全量事件，~3970 条）。

`Record.txt` 每行是一个事件的 Python `repr`（单引号、`True/False/None`），用
`ast.literal_eval(line)` 还原为 dict。从 websocket 实时收到的事件本身就是 dict，字段层级与本文一致。

---

## 1. 信封 (envelope)

每个事件的最外层结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `room_display_id` | int | 短房号 |
| `room_real_id` | int | 真实房号（本场与短房号相同） |
| `type` | str | 事件类型，与 `data['cmd']` 一致 |
| `data` | dict \| int \| None | 载荷，结构随 `type` 变化 |

按 `type` 路由。多数业务事件的内容在 `data['data']`（多包一层）；`DANMU_MSG` 例外，内容在
`data['info']`（位置数组）；部分系统事件 `data` 是标量或 `None`（如 `VIEW` 的 `data` 是整数）。

---

## 2. UserInfo —— 统一用户对象

新版事件内嵌一个统一的用户信息对象，形状一致，只是挂载位置不同。
**除 `GUARD_BUY` 外，所有携带用户的事件都可以用同一个函数解析它。**

### 2.1 挂载位置

| 事件 | UserInfo 路径 | 头像 | 粉丝牌 |
|------|--------------|:----:|:-----:|
| `DANMU_MSG` | `data.info[0][15].user` | ✓ | ✓ |
| `SEND_GIFT` | `data.data.sender_uinfo` | ✓ | ✓ |
| `SUPER_CHAT_MESSAGE` | `data.data.uinfo` | ✓ | ✓ |
| `LIKE_INFO_V3_CLICK` | `data.data.uinfo` | ✓ | ✓ |
| `ENTRY_EFFECT` | `data.data.uinfo` | ✓ | ✓ |
| `INTERACT_WORD_V2` | protobuf 字段 #22（见 §6） | ✓ | ✓ |
| `GUARD_BUY` | 无此对象，仅扁平 `uid`/`username` | ✗ | ✗ |

### 2.2 字段

```python
{
  'uid': 4408239,
  'base': {
    'name': 'Un_lucky',                 # 用户名
    'face': 'https://i0.hdslb.com/bfs/face/....jpg',   # 头像 URL
    'name_color_str': '#666666',         # 名字颜色，可能为 ''
    'origin_info': {'name': ..., 'face': ...},  # 见下方说明
    'is_mystery': false,                 # 神秘人隐身标记
  },
  'medal': {...} | None,                 # 粉丝牌，见 §3；None=未佩戴
  'guard': {'level': 0, 'expired_str': ''} | None,   # 大航海等级，level 0/1/2/3（B 站口径，见 §5.4）
  'wealth': {'level': 34, ...} | None,   # 荣耀等级（钱包等级），可能为 None
}
```

取值约定：

| 信息 | 取法 | 可空性 |
|------|------|--------|
| uid | `obj.uid` | 必有 |
| 用户名 | `obj.base.name` | 必有 |
| 头像 URL | `obj.base.face` | 必有（`GUARD_BUY` 除外，见下） |
| 粉丝牌 | `obj.medal` | **可为 `None`**（未戴本房牌） |
| 大航海等级 | **`obj.medal.guard_level`** | 见下方警告 |

> ⚠️ **大航海等级不要读 `obj.guard.level`。** 实测 `DANMU_MSG` 的 `user.guard` 恒为 `None`
> （792/792），`guard` 对象只在部分事件（如 SC 的 `uinfo`）里出现且常为 `{'level':0}`。
> 跨类型唯一可靠的来源是**粉丝牌里的 `guard_level`**（`UinfoMedal.guard_level` /
> `medal_info.guard_level`），B 站口径见 §5.4。本场 `DANMU_MSG.user.medal.guard_level` 分布：
> `0`（非舰）554、`3`（舰长）194、`2`（提督）2、无牌 42。代价是：用户若没戴本房粉丝牌
> （`medal is None`），就无法从弹幕判断其大航海等级。

> `origin_info`：本场 810 个用户对象中，`base.name/face` 与 `origin_info.name/face` 始终相同。
> 一般理解是 `is_mystery`（神秘人隐身）开启时 `base` 被替换为占位值、`origin_info` 保留原值，
> 但本场 `is_mystery` 全为 `false`，此差异未在数据中出现，无法证实。直接用 `base` 即可。

> `GUARD_BUY` 不含 UserInfo，也不含头像。仅有 `data.data.uid` 和 `data.data.username`。
> 若上舰场景需要头像/粉丝牌，需另行查询用户资料，事件本身不提供。

### 2.3 字段可空性（基于全量样本统计）

对核心事件逐字段扫描的结果。**样本量**：`DANMU_MSG` 792 条（结论可靠）；
`SEND_GIFT` 12 条、`SUPER_CHAT_MESSAGE` 6 条、`GUARD_BUY` 3 条（样本小，「必有」仅代表本场未见缺失）。

| 字段 | DANMU_MSG | SEND_GIFT | SUPER_CHAT | GUARD_BUY |
|------|-----------|-----------|------------|-----------|
| uid | 必有 | 必有 | 必有 | 必有 |
| 用户名 | 必有 | 必有 | 必有 | 必有 |
| 头像 face | 必有 | 必有 | 必有 | **无此字段** |
| 粉丝牌 medal | **None 占 42/792** | 本场都有¹ | 本场都有 | 无 |
| medal.guard_level | 有（随 medal） | 有 | 有 | 无 |
| guard 对象 | **恒 None** | None | `{'level':N}` | 无 |
| wealth 对象 | **恒 None** | （顶层 `wealth_level`） | None | 无 |
| 正文文本 | `info[1]` 必有 | — | `message` 必有 | — |
| 金额 | — | `price`/`total_coin` 必有 | `price` 必有 | `price` 必有 |

¹ `SEND_GIFT.sender_uinfo.medal` 本场 12 条都非 None，但用户不戴本房牌时一般会是 `None`，
仍需按可空处理（同一条事件里 `receiver_uinfo.medal` 就出现过 `None`）。

**实务建议（减少 fallback）**：

1. 用一个函数解析 §2.1 的统一对象，返回 `{uid, name, face, medal, guard_level}`。
   `DANMU_MSG`/`SEND_GIFT`/`SUPER_CHAT_MESSAGE`/`LIKE_INFO_V3_CLICK`/`ENTRY_EFFECT` 都走它，
   只是入口路径不同（见 §2.1 表）。
2. `GUARD_BUY` 单独构造：`{uid, name=username, face=None, medal=None, guard_level=guard_level}`。
3. `medal` 一律按**可能 None** 处理（无牌 → 前端渲染 `VtuRXiv:26xx.xxxx`）。
4. `guard_level` 统一从 `medal.guard_level` 取（见 §2.2 警告），不要读 `guard.level`。
5. 头像缺失（仅 `GUARD_BUY`）需有占位策略，事件本身给不出。

> 本场未出现的脏数据：匿名用户、缺 `uid`/缺 `price`、神秘人。现有解析里的「匿名用户/价格按 0」
> 兜底针对的就是这些——它们在生产可能出现，但**这份录制无法验证**，重写时建议保留这些防御。

---

## 3. 粉丝牌 (medal)

两种字段命名并存，语义相同。`UserInfo.medal` 用无前缀命名；`SEND_GIFT` / `SUPER_CHAT_MESSAGE`
顶层另有一个 `medal_info`，用 `medal_` 前缀命名。无粉丝牌时，前者为 `None`，后者 `medal_name` 为 `''`。

| 含义 | `UserInfo.medal` | `medal_info` | 示例 |
|------|------------------|--------------|------|
| 粉丝牌名 | `name` | `medal_name` | `埃抖露` |
| 等级 | `level` | `medal_level` | `33` |
| 大航海等级 | `guard_level` | `guard_level` | `3`（B 站口径，见 §5.4） |
| 主播 uid | `ruid` | `target_id` | `3546831533378448` |
| 是否点亮 | `is_light` | `is_lighted` | `1` |
| 主播名 | —（无） | `anchor_uname` | `埃瑟斯Asuse` |

其余为配色字段（`color*` / `v2_medal_color_*`）。

---

## 4. 核心业务事件

### 4.1 `DANMU_MSG` —— 弹幕

内容在 `data['info']`，是位置数组。推荐字段：

| 信息 | 路径 | 说明 |
|------|------|------|
| 文本 | `info[1]` | 字符串。表情弹幕时为表情的文字名（如 `升天`、`[妙]`） |
| 用户 | `info[0][15].user` | §2 的 UserInfo 对象 |
| 附加信息 | `info[0][15].extra` | **JSON 字符串**，需再 `json.loads` |
| 表情图 | `info[0][13]` | 普通弹幕为字符串 `'{}'`；表情弹幕为 dict（见下） |
| 发送时间 | `info[0][4]` | 毫秒时间戳 |
| 弹幕颜色 | `info[0][3]` | int |

`info[0][15].extra`（解码后）可用字段：

| 字段 | 说明 |
|------|------|
| `content` | 弹幕正文（与 `info[1]` 一致） |
| `reply_uname` | 被 @ 回复的用户名；`''` 表示非回复弹幕 |
| `reply_mid` | 被回复用户 uid |
| `emoticon_unique` | 非空表示表情弹幕 |
| `dm_type` | `0`=文字，`1`=表情 |

**表情弹幕**：当 `info[0][13]` 是 dict（而非字符串 `'{}'`）时，该弹幕是表情包图片：

```python
info[0][13] = {
  'url': 'http://i0.hdslb.com/bfs/live/edb36bc716dcc9a2bbc14b32c27b5af18ca81210.png',  # 图片 URL
  'width': 162, 'height': 162,
  'emoticon_unique': 'room_1921712061_86133',
  'is_dynamic': 0,
}
```

此时 `info[1]` 是该表情的文字名（caption）。判定：`isinstance(info[0][13], dict)`。

> 兼容性：本场 792 条弹幕全部含 `info[0][15].user`。老格式的位置数组
> `info[2]`（用户）、`info[3]`（粉丝牌，空 `[]` 表示无牌）仍存在，可作兜底，但字段索引不稳定。

本场分布：750 条带粉丝牌 / 42 条无牌；18 条回复弹幕；46 条表情弹幕。

### 4.2 `SEND_GIFT` —— 礼物

内容在 `data['data']`。

| 字段 | 类型 | 说明 |
|------|------|------|
| `uid` / `uname` | int / str | 送礼人（也可用 `sender_uinfo`，§2） |
| `giftName` / `giftId` | str / int | 礼物名 / id |
| `num` | int | 数量 |
| `coin_type` | str | `gold`=付费，`silver`=免费（如「粉丝团灯牌」） |
| `price` | int | 单价，单位**毫元**（÷1000 得元） |
| `total_coin` | int | 总价值，单位**毫元**；普通礼物 = `price × num` |
| `discount_price` | int | 折后单价，毫元 |
| `blind_gift` | obj \| None | 非 `None` 表示盲盒礼物 |
| `sender_uinfo` | obj | §2 UserInfo |
| `medal_info` | obj | §3 medal_info |
| `timestamp` | int | 送礼 unix 秒 |

金额口径（按本项目约定）：
- `coin_type == 'silver'`（免费礼物）按 **0 元**计。
- 盲盒礼物（`blind_gift` 非 `None`）按**开出面值** `total_coin` 计，而非实付 `price × num`。
- 其余 gold 礼物按 `total_coin`（= `price × num`）计。

本场出现的礼物：

| giftName | coin_type | price(毫元) | num | total_coin | 备注 |
|---|---|---:|---:|---:|---|
| 粉丝团灯牌 | silver | 1000 | 1 / 10 | 1000 / 10000 | 免费，计 0 元 |
| 小花花 | gold | 100 | 1 / 100 | 100 / 10000 | 0.1 元/个 |
| 你真好看 | gold | 1000 | 1 | 1000 | 1 元 |
| 人气票 | gold | 100 | 1 | 100 | |
| 666 | gold | 1000 | 1 | 1000 | |
| 幸运泡泡 | gold | 1500 | 1 | 5000 | 盲盒：实付 1.5 元，开出面值 5 元 |

### 4.3 `SUPER_CHAT_MESSAGE` —— 醒目留言 (SC)

内容在 `data['data']`。

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | str | SC 文字 |
| `message_trans` | str | 自动翻译，可能为 `''` |
| `price` | int | 金额，单位**元**（不是毫元也不是分） |
| `uid` | int | 发送人 |
| `uinfo` | obj | §2 UserInfo |
| `user_info` | obj | 旧版用户信息（`uname`/`face`/`user_level`…），与 `uinfo` 重复 |
| `medal_info` | obj | §3 medal_info |
| `time` | int | 在直播间停留秒数（由金额决定，见下） |
| `start_time` / `end_time` | int | 展示起止 unix 秒；`end - start == time` |
| `id` | int | SC id（用于与 `_JPN` 副本去重） |

**停留时长由金额（档位）决定**，`time` 字段即权威值：

| price(元) | time(秒) |
|---:|---:|
| 2 | 5 |
| 30 | 60 |

（更高价位停留更久，B 站官方档位。`time` 与 `end_time - start_time` 一致。）

### 4.4 `GUARD_BUY` —— 开通/续费大航海（上舰）

内容在 `data['data']`，字段扁平：

| 字段 | 类型 | 说明 |
|------|------|------|
| `uid` / `username` | int / str | 上舰用户 |
| `guard_level` | int | 大航海等级，B 站口径（见 §5.4） |
| `gift_name` | str | `舰长` / `提督` / `总督` |
| `num` | int | 购买月数 |
| `price` | int | 价格，单位**毫元**（如 `198000` = 198 元） |
| `start_time` / `end_time` | int | unix 秒 |

> `GUARD_BUY` **无法区分新开通 vs 续费**，也无 UserInfo / 头像。
> 「续费 / 新开通」需读伴随的 `USER_TOAST_MSG_V2`：其 `data.data.guard_info.op_type`
> （`2`=续费，本场 3 次上舰全部为续费），文案 `toast_msg` 也含「续费」字样。
> 因此本场没有「新开通」事件。

---

## 5. 单位与口径速查

### 5.1 金额单位

| 来源 | 字段 | 单位 |
|------|------|------|
| `SEND_GIFT` | `price` / `total_coin` / `discount_price` | 毫元（÷1000 = 元） |
| `GUARD_BUY` | `price` | 毫元（÷1000 = 元） |
| `SUPER_CHAT_MESSAGE` | `price` | 元 |

### 5.2 免费礼物
`SEND_GIFT.coin_type == 'silver'` 为免费礼物，金额计 0。

### 5.3 盲盒礼物
`SEND_GIFT.blind_gift != None`。金额按开出面值 `total_coin`，不是实付 `price × num`。

### 5.4 大航海等级映射
B 站原始 `guard_level` 与「舰长/提督/总督」的对应是**反序**的：

| B 站 `guard_level` | 角色 |
|:---:|:---:|
| 0 | 无 |
| 1 | 总督 |
| 2 | 提督 |
| 3 | 舰长 |

`GUARD_BUY.guard_level`、`UserInfo.guard.level`、medal 里的 `guard_level` 都用这套口径。

---

## 6. 互动事件

### 6.1 `INTERACT_WORD_V2` —— 进场 / 关注（protobuf）

载荷在 `data['data']['pb']`，是 base64 编码的 protobuf 二进制（明文字段已被移除）。

**解码方式**：项目内已附 `interact_word_v2.proto`（字段号由样本逆向，已用 `protoc --decode_raw`
和编译后解码全部 378 条样本验证）。用法：

```bash
protoc --python_out=. interact_word_v2.proto      # 生成 interact_word_v2_pb2.py
```

```python
import base64, interact_word_v2_pb2 as pb
m = pb.InteractWordV2()
m.ParseFromString(base64.b64decode(event['data']['data']['pb']))
m.msg_type          # 1=进场, 2=关注
m.uname             # 用户名
m.uinfo.base.face   # 头像 URL
m.uinfo.medal.name  # 粉丝牌名（无牌时为 ''）
m.uinfo.medal.level
```

`.proto` 里定义的关键字段：

| 字段 | 路径 | 说明 |
|------|------|------|
| uid | `#1` | |
| uname | `#2` | 用户名 |
| identities | `#4` | packed repeated，身份标记 |
| **msg_type** | `#5` | **1=进场，2=关注**（依据见下注） |
| roomid / timestamp | `#6` / `#7` | 秒 |
| 头像 | `#22.uinfo → #2.base → #2.face` | URL |
| 粉丝牌 | `#22.uinfo → #3.medal` | name@#1、level@#2、is_lighted@#9 |

> ⚠️ **两处粉丝牌布局不同**：顶层 `#9 fans_medal` 的 name 在 `#3`；而 `uinfo.medal`（`#22→#3`）的
> name 在 `#1`、level 在 `#2`。`.proto` 已分别用 `FansMedal` / `UinfoMedal` 两个 message 区分，别混用。

本场 378 条：377 条 `msg_type=1`，1 条 `msg_type=2`。

> **msg_type 枚举的推断依据**：本场 `LIKE_INFO_V3_CLICK` 是明文事件、其 `msg_type=6`，与老版
> `INTERACT_WORD`（非 V2）通用枚举 `1=进入 / 2=关注 / 3=分享 / 4=特别关注 / 5=互粉 / 6=点赞` 吻合，
> 说明 V2 复用了同一套枚举。据此 `1` 解为「进场」（占绝对多数，合理）；`2` 套用枚举解为「关注」，
> 但本场仅 1 例、未在数据内独立证实。

> 临时排查可直接 `protoc --decode_raw < 原始字节`（无需 `.proto`），输出按字段号列出原始结构。

> 同类的进场特效事件 `ENTRY_EFFECT` 仍是明文 dict（含 `data.data.uinfo`、`copy_writing` 文案），
> 但只覆盖舰长/高能用户的进场，非全量。

### 6.2 `LIKE_INFO_V3_CLICK` —— 点赞（单次）

明文 dict，内容在 `data['data']`：

| 字段 | 说明 |
|------|------|
| `uid` | 点赞用户 |
| `uinfo` | §2 UserInfo（含头像） |
| `fans_medal` | 粉丝牌（`medal_info` 风格） |
| `like_text` | 文案，如「为主播点赞了」 |
| `msg_type` | `6`（点赞） |
| `like_icon` | 点赞图标 URL |

### 6.3 `LIKE_INFO_V3_UPDATE` —— 点赞累计

`data['data'] = {'click_count': 32}`，本场点赞总数。

### 6.4 `WATCHED_CHANGE` —— 看过人数（累计）

`data['data'] = {'num': 38, 'text_small': '38', 'text_large': '38人看过'}`。
`num` 是累计「看过」人数，单调增长（本场 38 → 349）。与 §6.5 的在线人数（实时涨落）不同。

### 6.5 `ONLINE_RANK_COUNT` —— 高能榜在线人数

`data['data'] = {'count': 23, 'count_text': '23', 'online_count': 23, ...}`。

### 6.6 `VIEW`

`data` 直接是整数。**本场 296 条全部为 `1`**，无法从这份数据判断其语义（名义上是人气值/在线，
但此处恒为 1，未表现出累计或涨落特征）。与 §6.4 的「看过人数」是不同字段。

### 6.7 `ROOM_REAL_TIME_MESSAGE_UPDATE` —— 实时房间数据

`data['data'] = {'roomid': ..., 'fans': 5706, 'fans_club': 87, ...}`（粉丝数 / 粉丝团人数）。

---

## 7. 衍生 / 重复事件（解析时应忽略）

下列事件描述的对象已由其它事件覆盖，重复处理会导致重复计数：

| 事件 | 与之重复的事件 | 区别 |
|------|----------------|------|
| `SUPER_CHAT_MESSAGE_JPN` | `SUPER_CHAT_MESSAGE` | 同一 SC（`id` 相同）的日文翻译副本，多一个 `message_jpn` |
| `COMBO_SEND` / `COMBO_END` | `SEND_GIFT` | 连击的 UI 汇总；逐条 `SEND_GIFT` 已覆盖真实礼物 |
| `USER_TOAST_MSG` / `USER_TOAST_MSG_V2` | `GUARD_BUY` | 上舰滚动横幅；唯一额外信息是续费/新开通（`op_type`，见 §4.4） |

---

## 8. 完整事件类型清单（本场计数）

| 计数 | type | 类别 | 说明 |
|----:|------|------|------|
| 906 | `ONLINE_RANK_COUNT` | 互动 | 高能榜在线人数（§6.5） |
| 792 | `DANMU_MSG` | **核心** | 弹幕（§4.1） |
| 579 | `ONLINE_RANK_V3` | 噪声 | 高能榜列表（protobuf） |
| 387 | `WATCHED_CHANGE` | 互动 | 看过人数（§6.4） |
| 378 | `INTERACT_WORD_V2` | 互动 | 进场/关注（§6.1，protobuf） |
| 296 | `VIEW` | 互动 | 整数，本场恒为 1，语义不明（§6.6） |
| 274 | `STOP_LIVE_ROOM_LIST` | 噪声 | 平台级下播房间列表 |
| 122 | `ENTRY_EFFECT` | 互动 | 舰长/高能用户进场特效（§6.1 注） |
| 96 | `LIKE_INFO_V3_UPDATE` | 互动 | 点赞累计（§6.3） |
| 38 | `LIKE_INFO_V3_CLICK` | 互动 | 单次点赞（§6.2） |
| 33 | `NOTICE_MSG` | 噪声 | 全站/全区广播 |
| 16 | `ROOM_REAL_TIME_MESSAGE_UPDATE` | 互动 | 粉丝数等（§6.7） |
| 12 | `SEND_GIFT` | **核心** | 礼物（§4.2） |
| 9 | `COMBO_END` | 忽略 | 连击汇总（§7） |
| 6 | `SUPER_CHAT_MESSAGE` | **核心** | 醒目留言（§4.3） |
| 4 | `SUPER_CHAT_MESSAGE_JPN` | 忽略 | SC 日文副本（§7） |
| 3 | `GUARD_BUY` | **核心** | 上舰（§4.4） |
| 3 | `USER_TOAST_MSG` | 忽略 | 上舰横幅（§7） |
| 3 | `USER_TOAST_MSG_V2` | 忽略 | 上舰横幅（§7） |
| 2 | `LIKE_INFO_V3_NOTICE` | 噪声 | 点赞提示 |
| 2 | `WIDGET_BANNER` | 噪声 | 活动挂件横幅 |
| 2 | `GUARD_HONOR_THOUSAND` | 噪声 | 千舰荣誉 |
| 2 | `WIDGET_GIFT_STAR_PROCESS_V2` | 噪声 | 礼物星球进度挂件 |
| 1 | `VERIFICATION_SUCCESSFUL` | 系统 | 连接验证成功 |
| 1 | `COMMON_NOTICE_DANMAKU` | 噪声 | 通用通知弹幕 |
| 1 | `PLAYURL_RELOAD` | 系统 | 播放地址刷新 |
| 1 | `COMBO_SEND` | 忽略 | 连击汇总（§7） |
| 1 | `PREPARING` | **信号** | 下播（触发存档/报告，`data` 无业务字段） |
| 1 | `VOICE_JOIN_ROOM_COUNT_INFO` | 噪声 | 连麦人数 |
| 1 | `VOICE_JOIN_LIST` | 噪声 | 连麦列表 |
| 1 | `RANK_CHANGED` | 噪声 | 排名变化 |
| 1 | `POPULAR_RANK_CHANGED` | 噪声 | 人气排名变化 |
