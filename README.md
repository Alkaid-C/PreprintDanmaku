# PreprintDanmaku 预印本弹幕机

用于OBS的哔哩哔哩直播弹幕机。

前后端分离架构，后端基于[`bilibili-api`](https://github.com/Nemo2011/bilibili-api)。

开发中。LLM友好，可阅读`CLAUDE.md`获取关于本仓库的更多信息。

## 前端

目前自带两个前端，都在 `frontends/` 下：

- **`preprint`** —— arXiv 预印本论文风格的弹幕机。
- **`example`** —— 一个极简前端样例。

## FAQ

##### 为何重复造轮子？

想自己做前端，但[Laplace Chat](https://chat.laplace.live/)的文档看不懂（其实是根本没找到）。遂前后端都自己造。

##### 如何运行？

复制[`CLAUDE.md`](CLAUDE.md)给豆包，然后问：“豆包豆包，如何运行？”

##### 我想要一个长这样儿的弹幕机可以吗？

复制[`frontends/CLAUDE.md`](frontends/CLAUDE.md)和[`docs/SCHEMA.md`](docs/SCHEMA.md)给豆包，然后说：“豆包豆包，我想要一个长这样儿的弹幕机，帮我写一个。”

> 本项目中的[`example`极简样例前端](frontends/example/)就是ChatGPT在阅读了以上两个文档后生成的，运行正常。

## 许可证

本项目中除 `frontends/preprint/*` 以外的部分以 [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html) 协议开源。

`frontends/preprint/*`（`preprint` 前端）不在此开源协议范围内，保留所有权利，未经许可不得以任何方式使用。
