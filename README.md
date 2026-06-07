# DanmakuHime Preprint

学术论文(arXiv 预印本)风格的 B 站直播弹幕墙。

## 文件
| 文件 | 作用 |
|---|---|
| `app.py` | Flask 静态服务 + SSE + B 站二维码登录 + `LiveDanmaku` 事件转换。 |
| `preprint.html` | 前端入口。通过后端访问时订阅 `/stream`。 |
| `danmaku-feed.jsx` | React 组件和 SSE 适配层；报头字段由后端 `init` 填充。 |
| `SCHEMA.md` | 前后端字段契约。 |

## 运行

```bash
python3 app.py
```

启动后终端会打印二维码。用 B 站手机端扫码并确认登录，然后打开：

```text
http://127.0.0.1:19216/
```

默认配置：

- 直播间：`1921712061`
- 粉丝牌：`埃抖露`
- 端口：`19216`
- 左上角：`Bilibili:1921712061 [虚拟.日常]`
- 标题：`论灰域中的梦游态：一种非定域意识经验的现象学考察`
- 作者：
  - `埃瑟斯Asuse，环角事务所`
  - `埃斯卡尔Askr，环角事务所 ∗`

可以用参数覆盖：

```bash
python3 app.py --room-id 1921712061 --guard-name 埃抖露 --host 127.0.0.1 --port 19216
```

报头由后端 `init` 发送，只使用脚本顶部配置或命令行参数，不自动读取 B 站直播间标题/主播名。可用参数覆盖：

```bash
python3 app.py --stamp-label Bilibili --preprint-id 1921712061 --category 虚拟.日常 --title '论灰域中的梦游态：一种非定域意识经验的现象学考察'
```

调试礼物/SC 等事件解析时可以打开错误前推：

```bash
python3 app.py --debug
```

开启后，后端事件处理异常会作为前端可见的系统置顶消息显示。

也可以直接修改 `app.py` 顶部的 `Editable Configuration` 区域，所有常用配置都集中在那里。

## 事件映射

- `DANMU_MSG` → `danmaku` → References
- `SEND_GIFT` → `gift` → Acknowledgments
- `SUPER_CHAT_MESSAGE` → `superchat` → 顶部 Remark/Observation
- `GUARD_BUY` → `guard` → 顶部 Lemma/Theorem/Axiom

SC 显示规则：

- 金额 `< 30` 元 → `Remark`，否则 → `Observation`
- 停留时间：`<50` 元 60 秒，`<100` 元 2 分钟，`<500` 元 5 分钟，`<1000` 元 30 分钟，`<2000` 元 1 小时，更高 2 小时
