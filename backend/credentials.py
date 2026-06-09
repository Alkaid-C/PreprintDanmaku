#!/usr/bin/env python3
"""
DanmakuHime — Bilibili credential lifecycle.

Owns getting a usable Credential behind one public entry point: QR-code login
(delegated to bilibili_api.login_v2), JSON persistence with a freshness stamp,
and the load / refresh / re-login policy keyed on the credential's age.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional, Tuple

from bilibili_api import Credential, sync
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents

from initialization import AppConfig
from util import exception_summary

log = logging.getLogger("danmakuhime")


class CredentialManager:
    """Owns the whole Bilibili credential lifecycle behind one public entry point.

    Merges QR-code login (delegated to bilibili_api.login_v2.QrCodeLogin), JSON
    persistence with a freshness stamp, and the freshness policy that decides
    load / refresh / re-login. The single public method is obtain_credential().

    Freshness policy, keyed on the age of `obtained_at`:
      < load_max_age      -> load and use as-is
      load .. refresh_max -> refresh() and re-stamp
      >= refresh_max      -> re-login by QR
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._path = config.credential_file

    # ---- public entry point ------------------------------------------------

    def obtain_credential(self) -> Credential:
        """Return a usable Credential, applying the freshness policy with retries.

        Does 1 try + `initialization_retries` retries (sleeping login_retry_delay
        between), then gives up softly and returns an empty Credential() so startup
        can still proceed (anonymous connection), mirroring how the initial
        room_info fetch falls back to an empty shape. KeyboardInterrupt is not
        swallowed: it propagates so the caller (run()) can save stats and exit.
        """
        retries = self.config.initialization_retries
        for attempt in range(retries + 1):
            try:
                credential = self._resolve_credential()
                if credential is not None and credential.has_sessdata():
                    return credential
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                log.error("登录流程异常：%s", exception_summary(exc), exc_info=True)
            if attempt < retries:
                log.warning(
                    "登录未完成，%s 秒后重试（%s/%s）。按 Ctrl+C 退出。",
                    self.config.login_retry_delay_seconds, attempt + 1, retries,
                )
                time.sleep(self.config.login_retry_delay_seconds)
        log.warning("登录多次失败，使用空凭据继续（匿名连接）。")
        return Credential()

    # ---- freshness policy --------------------------------------------------

    def _resolve_credential(self) -> Optional[Credential]:
        """One pass of the freshness policy: load < 24h, refresh < 7d, else re-login."""
        if not self._path.exists():
            log.debug("未找到凭据文件，开始扫码登录。")
            return self._login_and_store()

        try:
            credential, obtained_at = self._load()
        except Exception as exc:
            log.warning("读取凭据失败：%s，改为扫码登录。", exc)
            return self._login_and_store()

        age = self._age_seconds(obtained_at)
        if age is None or age >= self.config.credential_refresh_max_age_seconds:
            log.debug("凭据已超过 7 天或时间戳缺失，重新扫码登录。")
            return self._login_and_store()

        if age < self.config.credential_load_max_age_seconds:
            log.debug("凭据在 %.1f 小时内，直接载入。", age / 3600)
            return credential

        log.debug("凭据已过 %.1f 小时，尝试刷新。", age / 3600)
        try:
            if sync(credential.check_refresh()):
                sync(credential.refresh())
                log.debug("凭据刷新完成。")
            else:
                log.debug("凭据仍有效，无需刷新，仅更新时间戳。")
            self._save(credential)
            return credential
        except Exception as exc:
            log.warning("刷新失败：%s，改为扫码登录。", exc)
            return self._login_and_store()

    def _login_and_store(self) -> Optional[Credential]:
        credential = self._qr_login_sync()
        if credential is None:
            return None
        try:
            self._save(credential)
            log.debug("凭据已保存：%s", self.config.credential_file)
        except Exception as exc:
            log.warning("保存凭据失败：%s", exc)
        return credential

    @staticmethod
    def _age_seconds(obtained_at: Optional[datetime]) -> Optional[float]:
        if obtained_at is None:
            return None
        return (datetime.now() - obtained_at).total_seconds()

    # ---- QR login (delegated to bilibili_api.login_v2.QrCodeLogin) ---------

    def _qr_login_sync(self) -> Optional[Credential]:
        try:
            return sync(self._qr_login())
        except Exception as exc:
            log.error("扫码登录失败：%s", exc, exc_info=True)
            return None

    async def _qr_login(self) -> Optional[Credential]:
        log.info("=== 登录 B 站账号 ===")
        login = QrCodeLogin()
        await login.generate_qrcode()
        self._show_qrcode(login)

        last_message = ""
        while True:
            await asyncio.sleep(self.config.login_poll_interval_seconds)
            state = await login.check_state()
            if state == QrCodeLoginEvents.DONE:
                print()  # close the \r poll line before the next log line
                log.info("登录成功。")
                return login.get_credential()
            if state == QrCodeLoginEvents.TIMEOUT:
                print()  # close the \r poll line before the next log line
                log.warning("二维码已失效。")
                return None
            if state == QrCodeLoginEvents.CONF:
                last_message = self._print_poll_message("已扫码，请在手机上确认...", last_message)
            else:  # QrCodeLoginEvents.SCAN
                last_message = self._print_poll_message("等待扫码...", last_message)

    def _show_qrcode(self, login: QrCodeLogin) -> None:
        # The QR block and the \r poll spinner below are interactive UI, not log
        # lines, so they stay on raw print().
        print("\n请使用 B 站手机客户端扫描二维码：")
        print(login.get_qrcode_terminal())
        try:
            login.get_qrcode_picture().to_file(str(self.config.qr_image_file))
            log.info("二维码图片已保存：%s", self.config.qr_image_file)
        except Exception as exc:
            log.warning("保存二维码图片失败：%s", exc)

    @staticmethod
    def _print_poll_message(message: str, last_message: str) -> str:
        if message != last_message:
            print(f"\r{message}", end="", flush=True)
        return message

    # ---- JSON persistence (incl. buvid3 + ac_time_value + freshness stamp) --

    def _load(self) -> Tuple[Credential, Optional[datetime]]:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        credential = Credential(
            sessdata=data.get("sessdata"),
            bili_jct=data.get("bili_jct"),
            buvid3=data.get("buvid3"),
            dedeuserid=data.get("dedeuserid"),
            ac_time_value=data.get("ac_time_value"),
        )
        obtained_at: Optional[datetime] = None
        raw = data.get("obtained_at")
        if raw:
            try:
                obtained_at = datetime.fromisoformat(raw)
            except ValueError:
                pass
        return credential, obtained_at

    def _save(self, credential: Credential) -> None:
        cookies = sync(credential.get_buvid_cookies())
        data = {
            "sessdata": cookies.get("SESSDATA", ""),
            "bili_jct": cookies.get("bili_jct", ""),
            "buvid3": cookies.get("buvid3", ""),
            "dedeuserid": cookies.get("DedeUserID", ""),
            "ac_time_value": credential.ac_time_value or "",
            "obtained_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
