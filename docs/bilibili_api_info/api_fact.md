# Bilibili 直播事件数据格式参考

> 本文档基于对 bilibili 直播 WebSocket 事件流（`bilibili_api` 的
> `live.LiveDanmaku(...).on("ALL")` 回调）的逆向整理，字段释义来自对真实直播录制的观察。
> 它不是官方文档，个别字段的语义为推断，已在相应位置标注。事件本身即 dict，字段层级与本文一致。

---

## 1. 信封 (envelope)

每个事件的最外层结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `room_display_id` | int | 短房号 |
| `room_real_id` | int | 真实房号 |
| `type` | str | 事件类型，与 `data['cmd']` 一致 |
| `data` | dict \| int \| None | 载荷，结构随 `type` 变化 |

按 `type` 路由。多数业务事件的内容在 `data['data']`（多包一层）；`DANMU_MSG` 例外，内容在
`data['info']`（位置数组）；部分系统事件 `data` 是标量或 `None`（如 `VIEW` 的 `data` 是整数）。

完整事件清单见 §15，按用途分组的详细字段见 §5–§13。

---

## 2. UserInfo —— 统一用户对象

新版事件内嵌一个统一的用户信息对象，形状一致，只是挂载位置不同。
**除 `GUARD_BUY` 外，携带用户的事件都可以用同一个函数解析它。**

### 2.1 挂载位置

| 事件 | UserInfo 路径 | 头像 | 粉丝牌 |
|------|--------------|:----:|:-----:|
| `DANMU_MSG` | `data.info[0][15].user` | ✓ | ✓ |
| `SEND_GIFT` | `data.data.sender_uinfo` | ✓ | ✓ |
| `SUPER_CHAT_MESSAGE` | `data.data.uinfo` | ✓ | ✓ |
| `LIKE_INFO_V3_CLICK` | `data.data.uinfo` | ✓ | ✓ |
| `ENTRY_EFFECT` | `data.data.uinfo`（完整对象，另有扁平 `uid`/`face`，见 §6.3） | ✓ | ✓ |
| `INTERACT_WORD_V2` | protobuf 字段 #22（见 §6.1） | ✓ | ✓ |
| `INTERACT_WORD` | `data.data.uinfo`（明文，见 §6.2） | ✓ | ✓ |
| `GUARD_BUY` | 无此对象，仅扁平 `uid`/`username` | ✗ | ✗ |

### 2.2 字段

```python
{
  'uid': 4408239,
  'base': {
    'name': 'Un_lucky',                  # 用户名
    'face': 'https://i0.hdslb.com/bfs/face/....jpg',   # 头像 URL
    'name_color_str': '#666666',         # 名字颜色，可能为 ''
    'origin_info': {'name': ..., 'face': ...},  # 见下方说明
    'is_mystery': False,                 # 神秘人隐身标记
  },
  'medal': {...} | None,                 # 粉丝牌，见 §3；None=未佩戴本房牌
  'guard': {'level': 0, 'expired_str': ''} | None,   # 大航海等级，level 0/1/2/3（见 §4.4）
  'wealth': {'level': 34, ...} | None,   # 荣耀等级（钱包等级），可能为 None
}
```

取值约定：

| 信息 | 取法 | 可空性 |
|------|------|--------|
| uid | `obj.uid` | 必有 |
| 用户名 | `obj.base.name` | 必有 |
| 头像 URL | `obj.base.face` | 必有（`GUARD_BUY` 除外） |
| 粉丝牌 | `obj.medal` | **可为 `None`**（未戴本房牌） |
| 大航海等级 | **`obj.medal.guard_level`** | 见下方警告 |

> ⚠️ **大航海等级不要读 `obj.guard.level`。** 观察到 `DANMU_MSG` 的 `user.guard` 恒为 `None`；
> `guard` 对象只在部分事件（如 SC 的 `uinfo`）里出现，且常为 `{'level': 0}`。跨类型唯一可靠的
> 来源是**粉丝牌里的 `guard_level`**（`UinfoMedal.guard_level` / `medal_info.guard_level`），B 站
> 口径见 §4.4。代价是：用户若没戴本房粉丝牌（`medal is None`），就无法从弹幕判断其大航海等级。

> `origin_info`：一般理解是 `is_mystery`（神秘人隐身）开启时 `base` 被替换为占位值、`origin_info`
> 保留原值。当 `is_mystery` 为 `False` 时两者一致，直接用 `base` 即可。

> `GUARD_BUY` 不含 UserInfo，也不含头像，仅有 `data.data.uid` 和 `data.data.username`。
> 若上舰场景需要头像/粉丝牌，需另行查询用户资料，事件本身不提供。

### 2.3 字段可空性

逐字段扫描核心事件的结论：

| 字段 | DANMU_MSG | SEND_GIFT | SUPER_CHAT | GUARD_BUY |
|------|-----------|-----------|------------|-----------|
| uid | 必有 | 必有 | 必有 | 必有 |
| 用户名 | 必有 | 必有 | 必有 | 必有 |
| 头像 face | 必有 | 必有 | 必有 | **无此字段** |
| 粉丝牌 medal | **可为 None** | 可为 None¹ | 通常有 | 无 |
| medal.guard_level | 有（随 medal） | 有 | 有 | 无 |
| guard 对象 | **恒 None** | None | `{'level':N}` | 无 |
| wealth 对象 | **恒 None** | （顶层 `wealth_level`） | None | 无 |
| 正文文本 | `info[1]` 必有 | — | `message` 必有 | — |
| 金额 | — | `price`/`total_coin` 必有 | `price` 必有 | `price` 必有 |

¹ 用户不戴本房牌时 `sender_uinfo.medal` 为 `None`；即便送礼也可能无牌，须按可空处理
（同一条事件里 `receiver_uinfo.medal` 也会出现 `None`）。

**实务建议（减少 fallback）**：

1. 用一个函数解析 §2.1 的统一对象，返回 `{uid, name, face, medal, guard_level}`。
   `DANMU_MSG`/`SEND_GIFT`/`SUPER_CHAT_MESSAGE`/`LIKE_INFO_V3_CLICK`/`INTERACT_WORD(_V2)` 都走它。
2. `GUARD_BUY` 单独构造：`{uid, name=username, face=None, medal=None, guard_level=guard_level}`。
3. `medal` 一律按**可能 None** 处理。
4. `guard_level` 统一从 `medal.guard_level` 取（见 §2.2 警告），不要读 `guard.level`。
5. 头像缺失（`GUARD_BUY`）需有占位策略，事件本身给不出。

> 可能出现但不一定每场都见的脏数据：匿名用户、缺 `uid`、缺 `price`、神秘人。解析里建议保留
> 「匿名用户 / 价格按 0」之类的防御性兜底。

---

## 3. 粉丝牌 (medal)

两种字段命名并存，语义相同。`UserInfo.medal` 用无前缀命名；`SEND_GIFT` / `SUPER_CHAT_MESSAGE`
顶层另有一个 `medal_info`，用 `medal_` 前缀命名。无粉丝牌时，前者为 `None`，后者 `medal_name` 为 `''`。

| 含义 | `UserInfo.medal` | `medal_info` | 示例 |
|------|------------------|--------------|------|
| 粉丝牌名 | `name` | `medal_name` | `埃抖露` |
| 等级 | `level` | `medal_level` | `33` |
| 大航海等级 | `guard_level` | `guard_level` | `3`（B 站口径，见 §4.4） |
| 主播 uid | `ruid` | `target_id` | `3546831533378448` |
| 是否点亮 | `is_light` | `is_lighted` | `1` |
| 主播名 | —（无） | `anchor_uname` | `埃瑟斯Asuse` |

其余为配色字段（`color*` / `v2_medal_color_*`）。

---

## 4. 单位与口径速查

### 4.1 金额单位

| 来源 | 字段 | 单位 |
|------|------|------|
| `SEND_GIFT` | `price` / `total_coin` / `discount_price` | 毫元（÷1000 = 元） |
| `GUARD_BUY` / `USER_TOAST_MSG(_V2)` | `price` | 毫元（÷1000 = 元） |
| `SUPER_CHAT_MESSAGE` | `price` | 元 |

### 4.2 免费礼物
`SEND_GIFT.coin_type == 'silver'` 为免费礼物（如「粉丝团灯牌」），金额计 0；`gold` 为付费。

### 4.3 盲盒礼物
`SEND_GIFT.blind_gift != None`。金额按开出面值 `total_coin`，不是实付 `price × num`。

### 4.4 大航海等级映射
B 站原始 `guard_level` 与「舰长/提督/总督」的对应是**反序**的：

| B 站 `guard_level` | 角色 |
|:---:|:---:|
| 0 | 无 |
| 1 | 总督 |
| 2 | 提督 |
| 3 | 舰长 |

`GUARD_BUY.guard_level`、`UserInfo.guard.level`、medal 里的 `guard_level` 都用这套口径。

---

## 5. 核心业务事件

### 5.1 `DANMU_MSG` —— 弹幕

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
| `color` / `font_size` / `mode` | 弹幕颜色 / 字号 / 模式（与 `info[0][3]`/`[2]`/`[1]` 冗余） |
| `id_str` | 弹幕唯一 id（字符串），可用于去重 |
| `emots` | dict \| None：**行内表情替换表**，键为 `[热]` 之类的文字记号，值含 `url`/`width`/`height`/`emoticon_id`。用于把正文里的 `[xx]` 渲染成小图标（与 §5.1 末的整条表情弹幕不同）。本场录制未出现，结构以 BAC 为准 |
| `user_hash` / `recommend_score` / `hit_combo` | 风控/推荐/连击辅助字段，一般不用 |

> 另有秒级发送时间：`info[9] = {'ct': '7C46A5F3', 'ts': 1781102927}`，`ts` 为 unix 秒（`info[0][4]` 是毫秒）。
> `info[0][15].user.base` 另有数字版名字颜色 `name_color`（int），与字符串版 `name_color_str` 并存。

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

> 兼容性：新格式的位置数组以 `info[0][15].user` 携带用户。老格式的 `info[2]`（用户）、
> `info[3]`（粉丝牌，空 `[]` 表示无牌）仍存在，可作兜底，但字段索引不稳定。

### 5.2 `SEND_GIFT` —— 礼物

内容在 `data['data']`。

| 字段 | 类型 | 说明 |
|------|------|------|
| `uid` / `uname` | int / str | 送礼人（也可用 `sender_uinfo`，§2） |
| `giftName` / `giftId` | str / int | 礼物名 / id |
| `num` | int | 数量 |
| `coin_type` | str | `gold`=付费，`silver`=免费 |
| `price` | int | 单价，单位**毫元**（÷1000 得元） |
| `total_coin` | int | 总价值，单位**毫元**；普通礼物 = `price × num` |
| `discount_price` | int | 折后单价，毫元 |
| `blind_gift` | obj \| None | 非 `None` 表示盲盒礼物 |
| `sender_uinfo` | obj | §2 UserInfo |
| `receiver_uinfo` | obj | 收礼人（主播） |
| `medal_info` | obj | §3 medal_info |
| `timestamp` | int | 送礼 unix 秒 |
| `rnd` / `tid` | str | 送礼时间戳 + 9 位随机后缀，两者通常相同；可作幂等键 |
| `magnification` | num | 倍率（盲盒/暴击相关），普通礼物为 `1` |
| `combo_send` / `batch_combo_send` | obj \| None | 连击聚合子对象（`combo_num`/`gift_name`/`uid` 等），仅展示用；真实礼物以本条逐发为准（去重见 §12.2） |

金额口径（按本项目约定）：
- `coin_type == 'silver'`（免费礼物）按 **0 元**计。
- 盲盒礼物（`blind_gift` 非 `None`）按**开出面值** `total_coin` 计，而非实付 `price × num`。
- 其余 gold 礼物按 `total_coin`（= `price × num`）计。

常见礼物举例：

| giftName | coin_type | price(毫元) | 备注 |
|---|---|---:|---|
| 粉丝团灯牌 | silver | 1000 | 免费，计 0 元 |
| 小花花 | gold | 100 | 0.1 元/个 |
| 你真好看 | gold | 1000 | 1 元 |
| 幸运泡泡 | gold | 1500 | 盲盒：实付 1.5 元，金额按开出面值 `total_coin` |

### 5.3 `SUPER_CHAT_MESSAGE` —— 醒目留言 (SC)

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
| `time` | int | 在直播间停留秒数（由金额决定） |
| `start_time` / `end_time` | int | 展示起止 unix 秒；`end - start == time` |
| `id` | int | SC id（用于与 `_JPN` 副本去重，见 §12.1） |

**停留时长由金额（档位）决定**，`time` 字段即权威值（与 `end_time - start_time` 一致）：

| price(元) | time(秒) |
|---:|---:|
| 2 | 5 |
| 30 | 60 |

更高价位停留更久（B 站官方档位）。

### 5.4 `GUARD_BUY` —— 开通/续费大航海（上舰）

内容在 `data['data']`，字段扁平：

| 字段 | 类型 | 说明 |
|------|------|------|
| `uid` / `username` | int / str | 上舰用户 |
| `guard_level` | int | 大航海等级，B 站口径（见 §4.4） |
| `gift_name` | str | `舰长` / `提督` / `总督` |
| `num` | int | 购买月数 |
| `price` | int | 价格，单位**毫元**（如 `198000` = 198 元） |
| `start_time` / `end_time` | int | unix 秒 |

> `GUARD_BUY` **无法区分新开通 vs 续费**，也无 UserInfo / 头像。「续费 / 新开通」需读伴随的
> `USER_TOAST_MSG_V2`（见 §9.1）：其 `guard_info.op_type`（`2`=续费），文案 `toast_msg` 也含
> 「续费 / 开通」字样。

---

## 6. 进场与互动事件

### 6.1 `INTERACT_WORD_V2` —— 进场 / 关注（protobuf）

载荷在 `data['data']['pb']`，是 base64 编码的 protobuf 二进制（明文字段已被移除）。

**解码方式**：项目内附 `interact_word_v2.proto`（字段号由样本逆向，已用 `protoc --decode_raw`
和编译后解码验证）：

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

> **msg_type 枚举的推断依据**：明文事件 `LIKE_INFO_V3_CLICK` 的 `msg_type=6`，与老版
> `INTERACT_WORD`（非 V2）通用枚举 `1=进入 / 2=关注 / 3=分享 / 4=特别关注 / 5=互粉 / 6=点赞`
> 吻合，说明 V2 复用了同一套枚举。`1`=进场（占绝大多数），`2`=关注。

> 临时排查可直接 `protoc --decode_raw < 原始字节`（无需 `.proto`），输出按字段号列出原始结构。

### 6.2 `INTERACT_WORD` —— 进场 / 关注（老版明文）

`INTERACT_WORD_V2` 的老版，内容在 `data['data']`，是明文 dict，结构与 V2 解码后一致：

| 字段 | 说明 |
|------|------|
| `uid` / `uname` | 用户 |
| `uinfo` | §2 UserInfo（含 `base.face`、`medal`） |
| `fans_medal` | 粉丝牌（`medal_info` 风格） |
| `msg_type` | `1`=进场 / `2`=关注（同 §6.1 枚举） |
| `identities` | 身份标记列表 |
| `spread_desc` / `spread_info` | 流量推广来源文案与配色，如 `流量包推广`、`#FF649E` |

明文 `INTERACT_WORD` 与 `INTERACT_WORD_V2` 可能并存，多见于流量包推广进场。全量进场以 §6.1 的 V2 为准。

### 6.3 `ENTRY_EFFECT` —— 进场特效

舰长 / 高能用户 / 荣耀等级较高用户进场时的横幅特效，**非全量进场**（普通观众不触发）。
内容在 `data['data']`，**含一个完整的 `uinfo`（§2 UserInfo），可走统一解析器**；另有一批扁平字段与之冗余：

| 字段 | 说明 |
|------|------|
| `uinfo` | §2 UserInfo（`base.face`、`base.name`、`medal`、`wealth`、`guard`）。**含粉丝牌**，可经 `medal.guard_level` 取大航海等级 |
| `uid` / `face` | 用户 uid 与头像 URL（与 `uinfo` 冗余） |
| `copy_writing` / `copy_writing_v2` | 文案，如 `<%秋瑟雪晞%> 来了` |
| `copy_color` / `highlight_color` | 文案配色 |
| `privilege_type` | 大航海等级，B 站口径（`0`=非舰高能/高荣耀，`1`/`2`/`3` 见 §4.4） |
| `wealthy_info` | `{'level': 26, ...}` 荣耀（钱包）等级 |
| `business` | 触发业务来源 |
| `basemap_url` / `web_basemap_url` | 特效底图 |

> `uinfo.medal` 可为 `None`（录制中约 7 成进场带本房粉丝牌）；带牌时 `medal.guard_level` 给出大航海等级。
> 与 §2.1 的其它事件一致，`uinfo` 路径形状相同，无需对 `ENTRY_EFFECT` 单独写扁平解析。

### 6.4 `LIKE_INFO_V3_CLICK` —— 点赞（单次）

明文 dict，内容在 `data['data']`：

| 字段 | 说明 |
|------|------|
| `uid` | 点赞用户 |
| `uinfo` | §2 UserInfo（含头像） |
| `fans_medal` | 粉丝牌（`medal_info` 风格） |
| `like_text` | 文案，如「为主播点赞了」 |
| `msg_type` | `6`（点赞） |
| `like_icon` | 点赞图标 URL |

### 6.5 `LIKE_INFO_V3_UPDATE` —— 点赞累计

`data['data'] = {'click_count': 187886}`，房间点赞累计总数，单调增长。

### 6.6 `LIKE_INFO_V3_NOTICE` —— 点赞提示

点赞相关的提示横幅，内容在 `data['data']`，是 UI 文案播报，不携带逐次点赞用户。
逐次点赞以 §6.4 为准，累计以 §6.5 为准。

### 6.7 `DM_INTERACTION` —— 互动聚合卡片

直播间滚动展示的「N 人正在点赞 / 投喂 / 分享」聚合卡片，是 UI 汇总而非单次互动。
内容在 `data['data']`，其中 `data['data']['data']` 是 **JSON 字符串**，需再 `json.loads`：

```python
data['data'] = {
  'cmd': 'DM_INTERACTION',
  'data': {
    'data': '{"fade_duration":10000,"cnt":5,"suffix_text":"人正在点赞","reset_cnt":1,"display_flag":1}',  # JSON 字符串
    'type': 106,    # 互动类别，见下表
    'id': 171455174236672,
    'status': 4,
    'dmscore': 36,
  }
}
```

内层 `type` 决定互动类别，与 JSON 里的 `suffix_text`、`cnt`（聚合人数）对应：

| `type` | suffix_text | 备注 |
|---:|---|---|
| 106 | 人正在点赞 | |
| 104 | 人在投喂 | JSON 额外带 `gift_id` / `gift_alert_message` |
| 105 | 人分享了直播间 | |

> 这些聚合数与逐条 `LIKE_INFO_V3_*` / `SEND_GIFT` / 分享事件指向同一批互动，按真实互动计数时
> 应以逐条事件为准，避免重复（见 §12）。

---

## 7. 在线 / 人数 / 实时数据

### 7.1 `WATCHED_CHANGE` —— 看过人数（累计）

`data['data'] = {'num': 9691, 'text_small': '9691', 'text_large': '9691人看过'}`。
`num` 是累计「看过」人数，单调增长。与 §7.2 的在线人数（实时涨落）不同。

### 7.2 `ONLINE_RANK_COUNT` —— 高能榜在线人数

`data['data'] = {'count': 2865, 'count_text': '2865', 'online_count': 2865, 'online_count_text': '2865'}`。
实时在线/高能人数，随观众进出涨落。

### 7.3 `ONLINE_RANK_V3` —— 高能榜列表（protobuf）

`data['data']['pb']` 是 base64 编码的 protobuf，内含高能榜前若干名的 uid、用户名、头像、贡献值。
是榜单明细的周期推送，数据量大；若只需在线人数用 §7.2 即可，无需解此榜。

### 7.4 `VIEW`

`data` 直接是整数，名义上是房间热度 / 人气指标。观察到的取值常恒为 `1`（本场录制 383/383 条均为 `1`），
未必反映真实人气，解析时按不可靠对待。与 §7.1 的「看过人数」是不同字段。

> 成因：它其实是 WS **心跳包回复**（操作码 3，正文为 uint32 人气值）被 `bilibili_api` surface 成的 `VIEW` 事件，
> 故 `data` 是裸整数而非 dict。心跳包本身由 `bilibili_api` 内部收发，无需我们处理。

### 7.5 `ROOM_REAL_TIME_MESSAGE_UPDATE` —— 实时房间数据

`data['data'] = {'roomid': ..., 'fans': 1714849, 'fans_club': 8214, 'red_notice': -1}`。
`fans`=粉丝数，`fans_club`=粉丝团人数，周期性更新。

---

## 8. 排行榜事件

### 8.1 `RANK_CHANGED` / `RANK_CHANGED_V2` —— 热门榜排名变化

主播在「热门榜」的排名播报，内容在 `data['data']`：

| 字段 | 说明 |
|------|------|
| `uid` | 主播 uid |
| `rank` | 当前排名（`0` 表示未上榜 / 已掉榜） |
| `rank_name_by_type` | 榜单名，如 `热门榜` |
| `countdown` | 距下次结算倒计时（秒） |
| `rank_type` / `sub_rank_type` | 榜单类型枚举 |
| `url` / `url_by_type` | 榜单 H5 地址 |

`RANK_CHANGED_V2` 是结构相同的新版（多 `rank_by_type` 等字段），二者可能并存。

### 8.2 `POPULAR_RANK_CHANGED` —— 人气榜排名变化

结构同 §8.1，`rank_name_by_type` 为 `人气榜`，另带 `cache_key`。是另一条榜单的排名播报。

---

## 9. 大航海衍生通知

这些事件伴随 `GUARD_BUY`（§5.4）出现，提供额外的展示 / 续费信息。

### 9.1 `USER_TOAST_MSG` / `USER_TOAST_MSG_V2` —— 上舰滚动横幅

上舰时的全屏 / 滚动横幅文案。`USER_TOAST_MSG` 字段扁平，`USER_TOAST_MSG_V2` 是结构化新版
（字段分组到 `guard_info` / `pay_info` / `effect_info` / `sender_uinfo` / `receiver_uinfo`）。

| 信息 | `USER_TOAST_MSG` | `USER_TOAST_MSG_V2` |
|------|------------------|---------------------|
| 文案 | `toast_msg` | `data.toast_msg` |
| 大航海等级 | `guard_level` | `guard_info.guard_level`（B 站口径，§4.4） |
| 角色名 | `role_name` | `guard_info.role_name`（`舰长`/`提督`/`总督`） |
| **新开通 / 续费** | `op_type` | `guard_info.op_type`（`2`=续费） |
| 月数 / 价格 | `num` / `price`（毫元） | `pay_info.num` / `pay_info.price`（毫元） |
| 送礼人 | `username` | `sender_uinfo`（含头像，可能为 `''`） |
| 收礼主播 | — | `receiver_uinfo` |
| 累计舰数 | `target_guard_count` | `guard_info.room_guard_count` |

`op_type` 是「新开通 vs 续费」的唯一可靠来源（`GUARD_BUY` 本身区分不了）。

### 9.2 `GUARD_HONOR_THOUSAND` —— 千舰荣誉成员增删

`data['data'] = {'add': [uid, ...], 'del': [uid, ...]}`。主播达成「千舰」时，其荣誉成员名单的
增量更新（`add` 新增、`del` 移除 uid 列表）。常只有一侧非空。

---

## 10. 横幅 / 挂件 / 通知

### 10.1 `NOTICE_MSG` —— 全站 / 全区广播

跨房间广播（如其它直播间的高价值礼物、抽奖等被推送到本房）。内容在 `data` 顶层：

| 字段 | 说明 |
|------|------|
| `id` / `name` | 广播模板 id / 名称（如 `万象天衣`） |
| `msg_common` | 通用文案，如 `<%A%>投喂<%B%>1个万象天衣，点击前往TA的房间吧！` |
| `msg_self` | 本房展示文案 |
| `full` / `half` / `side` | 不同展示形态的配色与图标（`background`/`color`/`highlight`/`time`/`head_icon`/`tail_icon`） |
| `roomid` / `real_roomid` | 事件**来源**房间（通常不是当前房间） |

`<%...%>` 是文案里的高亮占位（用户名 / 礼物名）。

### 10.2 `COMMON_NOTICE_DANMAKU` —— 通用通知弹幕

系统通知类弹幕，内容在 `data['data']`：

```python
data['data'] = {
  'content_segments': [
    {'text': '恭喜用户 酵母君_xmx <%荣耀等级升级至26级%>', 'type': 1,
     'font_color': '#CCCCCC', 'highlight_font_color': '#FFC73E', ...},
  ],
  'dmscore': 1008,
  'terminals': [1, 4, 5],   # 展示终端
}
```

文案由 `content_segments[].text` 拼接，`<%...%>` 为高亮片段。常见于荣耀等级升级等系统播报。

### 10.3 `WIDGET_BANNER` —— 活动挂件横幅

`data['data'] = {'timestamp': ..., 'widget_list': {'3061': None | {...}}}`。
`widget_list` 以挂件 id 为键，值为挂件配置或 `None`（无 / 关闭）。是直播间活动挂件位的状态推送。

### 10.4 `WIDGET_GIFT_STAR_PROCESS_V2` —— 礼物星球进度挂件

`data['data'] = {'name': '礼物星球', 'cur_num': 1, 'total_num': 9, 'version': ...}`。
「礼物星球」活动的进度（`cur_num`/`total_num`）。

---

## 11. 购物（小黄车）事件

带货直播间专有，普通直播间不出现。

### 11.1 `GOTO_BUY_FLOW` —— 下单提示

`data['data'] = {'text': '江**正在去买'}`。匿名化的「有人正在去买」滚动提示。

### 11.2 `SHOPPING_CART_SHOW` —— 购物车挂件显隐

`data['data'] = {'status': 2}`。购物车（小黄车）挂件的显示 / 隐藏状态切换。

---

## 12. 连击汇总 / 翻译副本

下列事件描述的对象已由其它事件覆盖，**按真实业务计数时应以原始事件为准**，否则会重复计数。

### 12.1 `SUPER_CHAT_MESSAGE_JPN` —— SC 日文副本

与 `SUPER_CHAT_MESSAGE`（§5.3）同一条 SC（`id` 相同）的日文翻译副本，多一个 `message_jpn`
字段（如 `このゲームイケメンは…`）。去重靠 `id`。

### 12.2 `COMBO_SEND` / `COMBO_END` —— 连击汇总

礼物连击的 UI 汇总，内容在 `data['data']`：含 `action`（如 `投喂`）、`gift_id` / `gift_name`、
`combo_num`（连击数）、`combo_total_coin`（连击总价值，毫元）、`medal_info` 等。
逐条 `SEND_GIFT`（§5.2）已覆盖真实礼物，连击事件用于展示连击动画，计数时忽略。

---

## 13. 系统 / 连接 / 平台事件

| 事件 | `data` | 说明 |
|------|--------|------|
| `VERIFICATION_SUCCESSFUL` | `None` | WebSocket 连接验证成功，连接建立后首条 |
| `PREPARING` | dict（`roomid`/`round`/`send_time`…，无业务字段） | 主播下播 / 切场。可作存档 / 报告触发信号 |
| `PLAYURL_RELOAD` | dict | 播放地址刷新通知，与播放器有关，无业务字段 |
| `STOP_LIVE_ROOM_LIST` | `{'room_id_list': [...]}` | 平台级下播房间 id 列表（全平台广播），与当前房间无关 |
| `VOICE_JOIN_LIST` | `{'apply_count', 'room_id', ...}` | 连麦申请列表状态 |
| `VOICE_JOIN_ROOM_COUNT_INFO` | `{'apply_count', 'notify_count', 'room_status', ...}` | 连麦人数 / 状态 |

---

## 14. 去重指引

按真实业务（弹幕 / 礼物 / SC / 上舰 / 互动）计数时，以下事件是同一对象的衍生或汇总，应只取
原始事件，避免重复：

| 衍生事件 | 原始事件 | 区别 / 唯一额外信息 |
|----------|----------|---------------------|
| `SUPER_CHAT_MESSAGE_JPN` | `SUPER_CHAT_MESSAGE` | `id` 相同的日文副本，多 `message_jpn`（§12.1） |
| `COMBO_SEND` / `COMBO_END` | `SEND_GIFT` | 连击 UI 汇总（§12.2） |
| `USER_TOAST_MSG` / `USER_TOAST_MSG_V2` | `GUARD_BUY` | 上舰横幅；唯一额外信息是 `op_type` 续费/新开通（§9.1） |
| `DM_INTERACTION` | `LIKE_INFO_V3_*` / `SEND_GIFT` / 分享 | 「N 人正在…」聚合卡片（§6.7） |
| `RANK_CHANGED_V2` | `RANK_CHANGED` | 同一榜单排名的新版播报（§8.1） |

---

## 15. 完整事件类型清单

按用途分组。各类型的字段详见对应小节。

| type | 分类 | 小节 |
|------|------|------|
| `DANMU_MSG` | 核心 · 弹幕 | §5.1 |
| `SEND_GIFT` | 核心 · 礼物 | §5.2 |
| `SUPER_CHAT_MESSAGE` | 核心 · SC | §5.3 |
| `GUARD_BUY` | 核心 · 上舰 | §5.4 |
| `INTERACT_WORD_V2` | 进场互动 · 进场/关注（protobuf） | §6.1 |
| `INTERACT_WORD` | 进场互动 · 进场/关注（明文） | §6.2 |
| `ENTRY_EFFECT` | 进场互动 · 进场特效 | §6.3 |
| `LIKE_INFO_V3_CLICK` | 进场互动 · 单次点赞 | §6.4 |
| `LIKE_INFO_V3_UPDATE` | 进场互动 · 点赞累计 | §6.5 |
| `LIKE_INFO_V3_NOTICE` | 进场互动 · 点赞提示 | §6.6 |
| `DM_INTERACTION` | 进场互动 · 互动聚合卡片 | §6.7 |
| `WATCHED_CHANGE` | 在线/人数 · 看过人数 | §7.1 |
| `ONLINE_RANK_COUNT` | 在线/人数 · 高能在线人数 | §7.2 |
| `ONLINE_RANK_V3` | 在线/人数 · 高能榜列表（protobuf） | §7.3 |
| `VIEW` | 在线/人数 · 人气整数 | §7.4 |
| `ROOM_REAL_TIME_MESSAGE_UPDATE` | 在线/人数 · 粉丝数等 | §7.5 |
| `RANK_CHANGED` / `RANK_CHANGED_V2` | 排行榜 · 热门榜 | §8.1 |
| `POPULAR_RANK_CHANGED` | 排行榜 · 人气榜 | §8.2 |
| `USER_TOAST_MSG` / `USER_TOAST_MSG_V2` | 大航海衍生 · 上舰横幅 | §9.1 |
| `GUARD_HONOR_THOUSAND` | 大航海衍生 · 千舰荣誉 | §9.2 |
| `NOTICE_MSG` | 横幅/挂件/通知 · 全站广播 | §10.1 |
| `COMMON_NOTICE_DANMAKU` | 横幅/挂件/通知 · 通用通知弹幕 | §10.2 |
| `WIDGET_BANNER` | 横幅/挂件/通知 · 活动挂件 | §10.3 |
| `WIDGET_GIFT_STAR_PROCESS_V2` | 横幅/挂件/通知 · 礼物星球进度 | §10.4 |
| `GOTO_BUY_FLOW` | 购物 · 下单提示 | §11.1 |
| `SHOPPING_CART_SHOW` | 购物 · 购物车显隐 | §11.2 |
| `SUPER_CHAT_MESSAGE_JPN` | 连击/副本 · SC 日文副本 | §12.1 |
| `COMBO_SEND` / `COMBO_END` | 连击/副本 · 连击汇总 | §12.2 |
| `VERIFICATION_SUCCESSFUL` | 系统 · 连接验证 | §13 |
| `PREPARING` | 系统 · 下播信号 | §13 |
| `PLAYURL_RELOAD` | 系统 · 播放地址刷新 | §13 |
| `STOP_LIVE_ROOM_LIST` | 系统 · 平台下播列表 | §13 |
| `VOICE_JOIN_LIST` | 系统 · 连麦列表 | §13 |
| `VOICE_JOIN_ROOM_COUNT_INFO` | 系统 · 连麦人数 | §13 |

---

## 16. 无法确认的其他事件

下列 `cmd` 由 BAC（bilibili-API-collect）文档列出，但**未在本项目的真实直播录制中出现**，
故未整理字段、未核验，仅留名备查。多为本项目用不到的场景（红包 / 天选 / 连麦 / 房管 / 带货 / 各类榜单等），
或与上文事件等价的老版本。需要时以 BAC `docs/live/message_stream.md` 为准。

```
LIVE  SUPER_CHAT_MESSAGE_DELETE  SPECIAL_GIFT  GIFT_STAR_PROCESS
WIDGET_GIFT_STAR_PROCESS  WIDGET_WISH_LIST  WIDGET_WISH_INFO
WELCOME  WELCOME_GUARD  ENTRY_EFFECT_MUST_RECEIVE  FULL_SCREEN_SPECIAL_EFFECT
ONLINE_RANK_V2  ONLINE_RANK_TOP3  LOG_IN_NOTICE
HOT_RANK_CHANGED  HOT_RANK_CHANGED_V2  HOT_RANK_SETTLEMENT  HOT_RANK_SETTLEMENT_V2
AREA_RANK_CHANGED  POPULAR_RANK_GUIDE_CARD
POPULARITY_RED_POCKET_START  POPULARITY_RED_POCKET_NEW  POPULARITY_RED_POCKET_WINNER_LIST
ANCHOR_LOT_CHECKSTATUS  ANCHOR_LOT_START  ANCHOR_LOT_END  ANCHOR_LOT_AWARD  ANCHOR_LOT_NOTICE
VOICE_JOIN_SWITCH  VIDEO_CONNECTION_JOIN_START  VIDEO_CONNECTION_MSG  VIDEO_CONNECTION_JOIN_END
UNIVERSAL_EVENT_GIFT  UNIVERSAL_EVENT_GIFT_V2  PLAY_TOGETHER  PLAYTOGETHER_ICON_CHANGE
REENTER_LIVE_ROOM  LIVE_MULTI_VIEW_NEW_INFO
ROOM_CHANGE  CHANGE_ROOM_INFO  ROOM_SKIN_MSG  ROOM_CONTENT_AUDIT_REPORT
ROOM_SILENT_ON  ROOM_SILENT_OFF  ROOM_BLOCK_MSG
ROOM_ADMINS  room_admin_entrance  ROOM_ADMIN_REVOKE
SUPER_CHAT_ENTRANCE  SYS_MSG  WARNING  CUT_OFF  CUT_OFF_V2
ANCHOR_ECOLOGY_LIVING_DIALOG  ANCHOR_BROADCAST  ANCHOR_HELPER_DANMU
PLAY_TAG  RECALL_DANMU_MSG  OTHER_SLICE_LOADING_RESULT
HOT_BUY_NUM  WEALTH_NOTIFY  USER_PANEL_RED_ALARM  GIFT_BOARD_RED_DOT
MESSAGEBOX_USER_MEDAL_CHANGE  MESSAGEBOX_USER_GAIN_MEDAL
FANS_CLUB_POKE_GIFT_NOTICE  master_qn_strategy_chg
```
