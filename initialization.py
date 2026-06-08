#!/usr/bin/env python3
"""
DanmakuHime — startup correctness: configuration + version/integrity guards.

Everything that has to be right *before* anything is served, in one place:
  - the path constants the backend resolves against,
  - AppConfig, the typed runtime-config container, and ConfigLoader, its only
    legitimate producer (reads config.toml, fails fast on any missing key),
  - VersionGuard, the two startup self-checks (backend ↔ backend.json, and the
    selected frontend folder ↔ its frontend.json).

The version/codename strings themselves live in main.py (the package's identity)
and are passed *into* VersionGuard as arguments — this module sits below main.py
in the import graph and must not import it back.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import pathspec

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.toml"
BACKEND_MANIFEST = BASE_DIR / "backend.json"

# The backend source files whose integrity is bound to backend.json (see
# build_backend.py). Resolved against BASE_DIR. MUST match build_backend.py's
# INTEGRITY_FILES exactly. Frontend files are not here — they live under
# frontends/<name>/ and are bound to that frontend's own frontend.json instead
# (see VersionGuard.check_frontend).
INTEGRITY_FILES = (
    "util.py",
    "initialization.py",
    "server.py",
    "credentials.py",
    "stats.py",
    "bilibili.py",
    "main.py",
)


# ==================== Configuration ====================
#
# `config.toml` (alongside this file) is the backend runtime config. There are no
# built-in defaults, env vars, or CLI flags — ConfigLoader.load() reads config.toml
# and fails fast if it is missing, unreadable, or short a key. AppConfig below is
# just the typed container the loader populates.


@dataclass
class AppConfig:
    # Livestream target
    room_id: int

    # Web server
    host: str
    port: int
    # Frontend package directory (holds index.html + frontend.json); resolved
    # relative to BASE_DIR. The backend serves and integrity-checks this folder.
    frontend: Path

    # Login (QR login is handled by bilibili_api.login_v2; we only poll it)
    login_poll_interval_seconds: int
    login_retry_delay_seconds: int
    # Retry budget shared by the two startup steps: credential login and the
    # initial room_info fetch. Each does 1 try + this many retries, then gives up
    # softly (empty credential / empty room_info) and keeps starting.
    initialization_retries: int

    # Credential persistence and freshness policy (see CredentialManager)
    credential_load_max_age_seconds: int      # < this: load as-is
    credential_refresh_max_age_seconds: int   # < this: refresh; else re-login

    # SSE buffering
    history_size: int
    subscriber_queue_size: int
    sse_heartbeat_seconds: int

    # Reconnect / report notices
    reconnect_delay_seconds: int
    reconnect_notice_dwell_seconds: int
    stream_end_report_dwell_seconds: int
    debug_forward_errors: bool
    debug_error_dwell_seconds: int

    # Event value conversion and pinned dwell
    gift_price_to_yuan_divisor: int
    cents_per_yuan: int
    # SuperChat below this amount is Remark; this amount and above is Observation.
    superchat_observation_threshold_yuan: int
    # SuperChat dwell = Bilibili's authoritative `time` (seconds) × this multiplier.
    superchat_dwell_multiplier: float
    guard_dwell_seconds_by_schema_level: Dict[int, int]

    # Output files (resolved relative to this module)
    event_log_file: Path
    stats_output_file: Path
    qr_image_file: Path
    credential_file: Path
    log_file: Path


class ConfigError(Exception):
    """config.toml is missing, unreadable, or missing/has a malformed key."""


class VersionMismatchError(Exception):
    """A manifest (backend.json or a frontend's frontend.json) is missing or
    unreadable, a version/api string disagrees, or a file hash does not match —
    i.e. a source file changed (or a version constant was bumped) without
    re-running the matching build script, or a mismatched front/back pair was
    combined."""


class ConfigLoader:
    """Builds the typed AppConfig from config.toml. There are no built-in defaults,
    env vars, or CLI flags: load() reads config.toml and fails fast (ConfigError →
    a clean SystemExit from main) if the file is missing, unparseable, or short any
    key. When adding a backend field, update config.toml, the AppConfig dataclass,
    and _TOML_SCALAR_FIELDS / _TOML_PATH_FIELDS together.
    """

    # Scalar config.toml keys that map 1:1 onto an AppConfig field of the same name.
    _TOML_SCALAR_FIELDS = (
        "room_id", "host", "port",
        "login_poll_interval_seconds", "login_retry_delay_seconds", "initialization_retries",
        "credential_load_max_age_seconds", "credential_refresh_max_age_seconds",
        "history_size", "subscriber_queue_size", "sse_heartbeat_seconds",
        "reconnect_delay_seconds", "reconnect_notice_dwell_seconds",
        "stream_end_report_dwell_seconds", "debug_forward_errors", "debug_error_dwell_seconds",
        "gift_price_to_yuan_divisor", "cents_per_yuan", "superchat_observation_threshold_yuan",
        "superchat_dwell_multiplier",
    )
    # Path keys: stored as bare names / relative paths in the TOML, resolved relative
    # to BASE_DIR. `frontend` is a directory (frontends/<name>); the rest are files.
    _TOML_PATH_FIELDS = (
        "frontend",
        "event_log_file", "stats_output_file", "qr_image_file", "credential_file", "log_file",
    )

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> AppConfig:
        """Build the backend runtime config from config.toml.

        Raises ConfigError if the file is missing, unparseable, or short any key.
        """
        if not path.exists():
            raise ConfigError(f"找不到配置文件 {path.name}（应与 main.py 放在同一目录）。")
        try:
            with open(path, "rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"配置文件 {path.name} 读取失败：{exc}") from exc

        # Basic keys sit at top level; advanced ones under [advanced]. Look in both so
        # an editor can move a key between sections without breaking it.
        advanced = data.get("advanced", {})

        def require(key: str) -> Any:
            if key in data:
                return data[key]
            if key in advanced:
                return advanced[key]
            raise ConfigError(f"配置文件 {path.name} 缺少必填项：{key}")

        try:
            guard_raw = require("guard_dwell_seconds_by_schema_level")
            guard = {int(k): int(v) for k, v in guard_raw.items()}
            kwargs: Dict[str, Any] = {key: require(key) for key in cls._TOML_SCALAR_FIELDS}
            kwargs.update({key: BASE_DIR / require(key) for key in cls._TOML_PATH_FIELDS})
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"配置文件 {path.name} 的某个值格式不对：{exc}") from exc

        return AppConfig(
            guard_dwell_seconds_by_schema_level=guard,
            **kwargs,
        )


class VersionGuard:
    """Startup staleness / consistency guards (not tamper-proofing) for the two
    independently-shipped packages: the backend (its .py modules ↔ backend.json, via
    check_version) and the selected frontend folder (index.html + payload ↔
    frontends/<name>/frontend.json, via check_frontend). Each catches editing a file
    or bumping a version without re-running the matching build script, or combining
    a mismatched front/back pair. Both entry points raise VersionMismatchError on any
    miss, which main() turns into a clean SystemExit.

    The version/codename strings live in main.py and are passed into these methods
    as arguments, so this module never imports main.py back.

    The json manifests are themselves unprotected, so this is a guard, not a lock.
    All hashing here MUST stay byte-identical to the build scripts (raw bytes via
    read_bytes(), no text decode / newline normalization): _file_sha256 mirrors
    build_backend.py, and _frontend_candidates + _frontend_group_hash mirror
    build_frontend.py's NON_PAYLOAD filter + pathspec matching + group digest.
    """

    # Shown to the operator for any file-hash failure (missing/unrecorded/unreadable
    # hash, or a content mismatch). The specific cause is appended after it.
    INTEGRITY_FAIL_MESSAGE = "文件完整性校验失败，请联系开发者。"

    # Files that live in a frontend folder but are never part of the hashed payload:
    # package metadata/tooling and user-editable frontend-local configuration.
    # Skipped from the candidate set so a greedy pattern can't pull them in — MUST
    # match build_frontend.py's NON_PAYLOAD exactly.
    _FRONTEND_NON_PAYLOAD = frozenset({"frontend.json", "build_frontend.py", ".project", "config.json"})

    # ---- backend self-check (backend modules ↔ backend.json) ---------------

    @classmethod
    def check_version(cls, app_version: str, release_date: str, api_version: str) -> None:
        """Fail fast unless the given app_version / release_date / api_version and the
        sha256 of every INTEGRITY_FILES entry match what build_backend.py recorded in
        backend.json. Catches editing a backend module (or bumping a constant)
        without re-running `python3 build_backend.py`. The constants are passed in by
        main.py, where they are defined.
        """
        manifest = cls._load_manifest(BACKEND_MANIFEST, "python3 build_backend.py")
        for label, expected in (
            ("app_version", app_version),
            ("release_date", release_date),
            ("api_version", api_version),
        ):
            if manifest.get(label) != expected:
                raise VersionMismatchError(
                    f"{label} 不匹配：main.py 为 {expected!r}，"
                    f"{BACKEND_MANIFEST.name} 为 {manifest.get(label)!r}。请运行 `python3 build_backend.py`。"
                )
        cls._verify_hashes(manifest.get("hashes"), BASE_DIR, INTEGRITY_FILES, BACKEND_MANIFEST.name)

    # ---- frontend check (frontends/<name>/ ↔ frontend.json) ----------------

    @classmethod
    def check_frontend(cls, config: AppConfig, api_version: str) -> Dict[str, Any]:
        """Fail fast unless the selected frontend folder carries a frontend.json whose
        api_version equals the backend's api_version (passed in by main.py) and whose
        every `payload` group (one hash per `.project` pattern) re-derives to the same
        digest on disk. Returns the manifest (for startup logging).

        This is what makes the front/back split work: any frontend package with a
        matching api_version drops in, but a stale or mismatched one is rejected
        before anything is served. The mismatch message names the offending pattern,
        so per-file granularity is the author's choice of how finely `.project` slices.
        """
        frontend_dir = config.frontend
        label = f"{frontend_dir.name}/frontend.json"
        if not (frontend_dir / "index.html").is_file():
            raise VersionMismatchError(
                f"前端目录 {frontend_dir} 缺少 index.html（config.toml 的 frontend 指向是否正确？）。"
            )
        manifest = cls._load_manifest(
            frontend_dir / "frontend.json", f"python3 frontends/build_frontend.py {frontend_dir.name}"
        )

        api = manifest.get("api_version")
        if api != api_version:
            raise VersionMismatchError(
                f"前后端 API 版本不一致：后端为 {api_version!r}，"
                f"前端 {frontend_dir.name} 需要 {api!r}。请改用 API 版本相符的前端或后端包。"
            )

        payload = manifest.get("payload")
        if not isinstance(payload, dict) or not payload:
            raise VersionMismatchError(
                f"{cls.INTEGRITY_FAIL_MESSAGE}（{label} 缺少 payload 字段）"
            )

        candidates = cls._frontend_candidates(frontend_dir)
        for pattern, expected in payload.items():
            if cls._frontend_group_hash(frontend_dir, pattern, candidates) != expected:
                raise VersionMismatchError(
                    f"{cls.INTEGRITY_FAIL_MESSAGE}（前端 {pattern!r} 这组文件与清单不一致）"
                )
        return manifest

    # ---- shared manifest loading -------------------------------------------

    @staticmethod
    def _load_manifest(path: Path, rebuild_hint: str) -> Dict[str, Any]:
        """Read and JSON-parse a manifest, mapping every failure to a clear
        VersionMismatchError. `rebuild_hint` is the build command to re-run."""
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise VersionMismatchError(f"找不到 {path.name}，请先运行 `{rebuild_hint}` 生成清单。")
        except (OSError, json.JSONDecodeError) as exc:
            raise VersionMismatchError(f"无法读取 {path.name}：{exc}")
        if not isinstance(manifest, dict):
            raise VersionMismatchError(f"{path.name} 格式不对（应为 JSON 对象）。")
        return manifest

    # ---- backend hashing (one digest per file, mirrors build_backend.py) ---

    @classmethod
    def _verify_hashes(cls, hashes: Any, base_dir: Path, names, manifest_name: str) -> None:
        """Check every file in `names` against its recorded sha256 in `hashes`,
        resolving paths against base_dir. Raises VersionMismatchError on any miss."""
        if not isinstance(hashes, dict):
            raise VersionMismatchError(
                f"{cls.INTEGRITY_FAIL_MESSAGE}（{manifest_name} 缺少 hashes 字段）"
            )
        for name in names:
            expected = hashes.get(name)
            if expected is None:
                raise VersionMismatchError(
                    f"{cls.INTEGRITY_FAIL_MESSAGE}（{manifest_name} 未记录 {name} 的哈希）"
                )
            path = base_dir / name
            try:
                actual = cls._file_sha256(path)
            except FileNotFoundError:
                raise VersionMismatchError(
                    f"{cls.INTEGRITY_FAIL_MESSAGE}（找不到文件 {name}）"
                )
            except OSError as exc:
                raise VersionMismatchError(
                    f"{cls.INTEGRITY_FAIL_MESSAGE}（无法读取 {name} 进行哈希：{exc}）"
                )
            if actual != expected:
                raise VersionMismatchError(
                    f"{cls.INTEGRITY_FAIL_MESSAGE}（{name} 哈希不匹配，文件内容与清单不一致）"
                )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        """sha256 of a file's raw bytes, hex-encoded. Must stay byte-identical to the
        build scripts' hashing (raw bytes, no text decode / newline normalization)."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    # ---- frontend hashing (one digest per `.project` group, mirrors build_frontend.py) ---

    @classmethod
    def _frontend_candidates(cls, frontend_dir: Path) -> List[Path]:
        """Files under the frontend dir eligible to be matched by a `.project` pattern
        (minus the non-payload tooling/artifacts). Mirrors build_frontend.py."""
        files = []
        for path in frontend_dir.rglob("*"):
            if not path.is_file() or path.suffix == ".zip":
                continue
            rel = path.relative_to(frontend_dir)
            if "__pycache__" in rel.parts or rel.as_posix() in cls._FRONTEND_NON_PAYLOAD:
                continue
            files.append(path)
        return files

    @staticmethod
    def _frontend_group_hash(frontend_dir: Path, pattern: str, candidates: List[Path]) -> str:
        """One sha256 over the candidates matched by `pattern`, sorted by relative posix
        path, each contribution = path + NUL + raw bytes + NUL. Byte-identical to
        build_frontend.py's match_pattern + group_hash."""
        spec = pathspec.PathSpec.from_lines("gitwildmatch", [pattern])
        matched = sorted(
            (p for p in candidates if spec.match_file(p.relative_to(frontend_dir).as_posix())),
            key=lambda p: p.relative_to(frontend_dir).as_posix(),
        )
        digest = hashlib.sha256()
        for path in matched:
            digest.update(path.relative_to(frontend_dir).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()
