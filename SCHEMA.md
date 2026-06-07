# 弹幕预印本 · SSE Schema

后端通过 SSE 推送一条事件流;前端把每个事件渲染成 arXiv 预印本里的一种排版元素。
本文件是**前后端的字段契约**。配色/排版不影响协议。

---

## 0. 传输

- 单条 SSE `data:` 负载 = 一个 **JSON 对象**(一个事件)。
- 事件按发生顺序推送;前端依赖到达顺序维护队列。
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
| `timestamp` | string | 事件时间,`HH:MM`(渲染成引用里的"出版年") |

> `init` 没有 `sender`;其余四类都带 `sender`。

---

## 2. `sender`(身份字典)

| 字段 | 类型 | 说明 | 映射 |
|---|---|---|---|
| `username` | string | 用户名(可打码,如 `迟***`) | 作者署名 |
| `badgename` | string | 粉丝牌名;**空串 `""` = 无牌** | 期刊名(无牌 → `VtuRXiv`) |
| `badgelevel` | int | 粉丝牌等级 | 卷号 `Vol. N` |
| `guardstat` | int | 大航海身份 `0/1/2/3` = 无 / 舰长 / 提督 / 总督 | 署名上标 `†` / `‡` / `§` |

- `badgename:""` → 该条目渲染为 `VtuRXiv:26xx.xxxx (HH:MM)`(尚未在某粉丝牌"期刊"发表的预印本)。
- `guardstat` 决定**普通条目里名字后面的上标**:`1→†`(舰长) `2→‡`(提督) `3→§`(总督)。

---

## 3. 各事件类型

### 3.1 `danmaku` — 普通弹幕 → **参考文献条目**
渲染在 **References** 栏(滚动列,底部进、顶部裁切)。

| 字段 | 类型 | 说明 |
|---|---|---|
| `sender` | dict | 见上 |
| `text` | string | 弹幕正文(通常 < 40 字符) |

> 渲染示例:`迟***‡. "龙国出处原来是网文吗." 埃抖露, Vol. 23 (21:03).`
> 左边距带连续行号(lineno)。

### 3.2 `gift` — 礼物 → **资助致谢(Acknowledgments)**
渲染在底部 **Acknowledgments** 栏。无独立时长,生命周期由 FIFO 队列决定(见 §4)。

| 字段 | 类型 | 说明 |
|---|---|---|
| `sender` | dict | 见上 |
| `giftname` | string | 礼物名 |
| `giftcount` | int | 数量(连击数),≥ 1 |
| `gifttotalvalue` | int | 总价值(**分**) |

> 渲染示例:`Funding. 张*** via 小星星 ×30.`
> `gifttotalvalue` 当前不直接显示(保留字段)。

### 3.3 `superchat` — SuperChat → **Remark / Observation 框**
渲染在顶部**钉住栏**,按 `dwell_seconds` 停留后淡出。

| 字段 | 类型 | 说明 | 映射 |
|---|---|---|---|
| `sender` | dict | 见上 | |
| `level` | int | `1` / `2` | `1→Remark` `2→Observation` |
| `dwell_seconds` | int | 钉住秒数 | 钉住时长(后端权威) |
| `value` | int | 金额(**分**) | 框内显示 `¥N` |
| `text` | string | 留言正文 | 框内陈述(斜体) |

> 渲染示例:`Remark 12 (老***, ¥100). 这个反例我想了三天，今天终于听懂了。`

### 3.4 `guard` — 上船(大航海)→ **Lemma / Theorem / Axiom 框**
渲染在顶部**钉住栏**,同 SuperChat。

| 字段 | 类型 | 说明 | 映射 |
|---|---|---|---|
| `sender` | dict | 见上 | |
| `level` | int | `1` / `2` / `3` = 舰长 / 提督 / 总督 | `1→Lemma` `2→Theorem` `3→Axiom` |
| `newguard` | bool | `true` 开通 / `false` 续费 | 文案动词 |
| `months` | int | 开通/续费的月数 | 文案 |
| `dwell_seconds` | int | 钉住秒数 | 钉住时长 |

> 渲染示例:`Axiom 3 (范***§). 续费了 6 个月的总督.`
> 文案 = `{开通了|续费了} {months}个月的{舰长|提督|总督}`(从 `level` 推导名称)。

### 3.5 `init` — 初始化 → **报头 / masthead**

| 字段 | 类型 | 必填 | 说明 | 映射 |
|---|---|---|---|---|
| `stamp_label` | string | — | 左上角来源标签,如 `Bilibili` | 左上角前缀 `Bilibili:` |
| `preprint_id` | string | — | 条目标识,如 `1921712061` | 左上角 `stamp_label:preprint_id` |
| `category` | string | — | 分类,如 `虚拟.日常` | 左上角 `[...]` |
| `authors` | array | — | 作者列表;每项可含 `name` / `affiliation` / `corresponding` | 作者署名;通讯作者显示 `∗` |
| `anchor` | string | — | 兼容旧字段: 单行作者名 | 作者署名 |
| `room_title` | string | — | 直播间标题 | 论文大标题(中文衬线) |

> **日期**前端自动取系统当天,无需传。
> 前端没有这些报头字段默认值;后端需要通过 `init` 下发。

---

## 4. 队列机制(前端行为,后端无需关心)

- **弹幕 + 礼物共享一个定容 FIFO 队列**(共享序列)。每来一个新事件,其所属栏"入场";同时**最老的一个事件**(无论类型)从其所属栏"退场" —— 一进一出。
- 屏幕上消失顺序 = 序列顺序。两栏各自表现:
  - **References(弹幕)**:新弹幕底部进,最老弹幕顶部裁切(无感退场)。
  - **Acknowledgments(礼物)**:新礼物进 → 栏高上升;最老礼物退 → 栏高塌缩(可见)。
- **SuperChat / guard** 不进 FIFO 队列;进**顶部钉住栏**,按 `dwell_seconds` 倒计时(框顶有进度细条),**最多 3 条,老的先走**。
- 五种环境(Remark < Observation < Lemma < Theorem < Axiom)**共享一个编号计数器**。

---

## 5. 映射速查

| 事件 | 排版元素 | 位置 |
|---|---|---|
| danmaku | 参考文献条目 `[作者. "正文." 期刊, Vol. N (时间).]` | References 栏(滚动) |
| gift | `Funding.` 致谢行 | Acknowledgments 栏(底部) |
| superchat L1/L2 | **Remark / Observation** 框(带 ¥) | 顶部钉住栏 |
| guard L1/L2/L3 | **Lemma / Theorem / Axiom** 框 | 顶部钉住栏 |
| init | 报头(标题/作者/期刊/戳) | masthead |
| `sender.guardstat` | 署名上标 `† ‡ §` | 各条目内 |
| `sender.badgename=""` | `VtuRXiv:…` 预印本号 | 代替期刊+卷号 |

---

## 6. 接入说明

前端 `useDanmakuStream` 只连接后端 SSE:

```js
const es = new EventSource('/stream');
es.onmessage = (m) => {
  const ev = JSON.parse(m.data);
  if (ev.type === 'init') applyInit(ev);   // 填充 DM_META
  else emit(adapt(ev));                     // adapt(): 后端字段 → 内部事件形状
};
```

- `adapt()` 负责字段改名/缺省填充(如 `badgename:"" → preid`、`level → 环境名`)。
- 没有离线模拟器;需要通过后端服务访问页面。
