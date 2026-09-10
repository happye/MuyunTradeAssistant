"""Chat 会话本地持久化（ISS-092：聊天中断恢复）。

职责（纯文件 IO，不含对话逻辑）：
- current.json 保存当前会话完整状态（messages + 元数据），原子写（tmp + os.replace），
  崩溃在任何点上盘上的文件都是完整可解析的。
- archive_current() 把当前会话重命名为 session_YYYYMMDD_HHMMSS.json 留档——
  新会话启动/重置时旧对话归档而非删除/覆盖，防静默丢数据。
- load()/peek() 容错：JSON 损坏/结构非法时重命名为 corrupt_ 前缀留档（不删除用户数据），
  返回 None 视为无可恢复会话。
- 目录 ~/.muyun/chat_sessions/（与 news_cache/kline_cache/benzong_cache 同区）。

agent.py 的 ChatAgent 持有本类实例并决定何时 save；REPL 决定恢复 UX。
"""
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 会话文件结构版本（未来格式变更时用于迁移判断）
SESSION_FILE_VERSION = 1

# messages 内合法角色（结构校验用；其余字段宽松——tool 消息带 tool_call_id 等）
_VALID_ROLES = {"system", "user", "assistant", "tool"}


class SessionStore:
    """chat 会话文件的读写与归档。所有方法线程安全（内部 IO 锁）。"""

    def __init__(self, base_dir: Optional[Path] = None):
        # base_dir 仅供测试注入临时目录；默认与项目其它缓存同区
        self._dir = Path(base_dir) if base_dir else (
            Path.home() / ".muyun" / "chat_sessions")
        self._io_lock = threading.Lock()

    # ── 路径 ──────────────────────────────────────────────

    @property
    def dir(self) -> Path:
        return self._dir

    @property
    def current_path(self) -> Path:
        return self._dir / "current.json"

    def current_exists(self) -> bool:
        return self.current_path.exists()

    # ── 写 ───────────────────────────────────────────────

    def save(self, messages: list, model: str = "",
             created_at: Optional[str] = None) -> dict:
        """原子写 current.json，返回写入的元数据（含补齐后的 created_at）。

        created_at 为 None 时取当前时间（新会话）；调用方（ChatAgent）在
        resume 后传入原会话的 created_at 保持连续。
        OSError 向上抛，由调用方决定告警策略。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if created_at is None:
            created_at = now
        meta = {
            "version": SESSION_FILE_VERSION,
            "created_at": created_at,
            "updated_at": now,
            "model": model,
            "messages": messages,
        }
        with self._io_lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self.current_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.current_path)
        return meta

    # ── 读 ───────────────────────────────────────────────

    def peek(self) -> Optional[dict]:
        """读 current.json 元数据（不含消息体）。无文件/损坏 → None。"""
        with self._io_lock:
            data = self._read_current()
        if data is None:
            return None
        return {
            "n_messages": self._count_conversation(data),
            "updated_at": data.get("updated_at", ""),
            "created_at": data.get("created_at", ""),
            "model": data.get("model", ""),
        }

    def load(self) -> Optional[dict]:
        """读 current.json 全量（messages + 元数据）。无文件/损坏 → None。"""
        with self._io_lock:
            return self._read_current()

    def _read_current(self) -> Optional[dict]:
        """读并校验 current.json（须持 _io_lock 调用）。

        损坏（JSON 解析失败/结构非法）→ 重命名 corrupt_ 前缀留档后返回 None，
        不删除用户数据、不抛异常。
        """
        path = self.current_path
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not self._is_valid(data):
                raise ValueError("会话文件结构非法（messages 非法或为空）")
            return data
        except (json.JSONDecodeError, ValueError, OSError) as e:
            # 隔离 rename 本身也可能失败（Windows 文件被其他进程独占），
            # 失败时留原地下次再试，不能让异常穿出杀掉 chat 启动
            try:
                renamed = self._rename_unique(path, "corrupt_")
                logger.warning(
                    f"chat会话文件损坏已隔离（{renamed.name}，原文件保留可人工检查）: {e}")
            except OSError as e2:
                logger.warning(
                    f"chat会话文件读取/隔离失败（文件保留原地，可能被其他程序占用）: {e} / {e2}")
            return None

    @staticmethod
    def _is_valid(data) -> bool:
        """结构校验：dict 且 messages 为非空 list[dict]，角色合法。"""
        if not isinstance(data, dict):
            return False
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return False
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in _VALID_ROLES:
                return False
        return True

    @staticmethod
    def _count_conversation(data: dict) -> int:
        """非 system 消息数（system 是代码常量，不算对话内容）。"""
        return sum(
            1 for m in data.get("messages", [])
            if isinstance(m, dict) and m.get("role") != "system"
        )

    # ── 归档 ─────────────────────────────────────────────

    def archive_current(self) -> Optional[Path]:
        """current.json 重命名为 session_YYYYMMDD_HHMMSS.json（撞名加 _2/_3）。

        无 current.json → None。OSError（文件被占用等）向上抛由调用方告警。
        """
        with self._io_lock:
            if not self.current_path.exists():
                return None
            return self._rename_unique(self.current_path, "session_")

    def _rename_unique(self, path: Path, prefix: str) -> Path:
        """重命名到同目录，时间戳为名，撞名加 _2/_3 后缀（须持 _io_lock 调用）。"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self._dir / f"{prefix}{ts}.json"
        i = 2
        while target.exists():
            target = self._dir / f"{prefix}{ts}_{i}.json"
            i += 1
        path.rename(target)
        return target

    # ── 列表 ────────────────────────────────────────────

    def list_sessions(self) -> list:
        """列出全部会话文件（current + 归档 + corrupt 隔离件），按 mtime 倒序。

        单个文件解析失败不影响列表（显示为条数未知）。
        """
        entries = []
        with self._io_lock:
            try:
                files = sorted(
                    (p for p in self._dir.glob("*.json") if p.is_file()),
                    key=lambda p: p.stat().st_mtime, reverse=True)
            except OSError:
                return []
            for p in files:
                n = None
                try:
                    updated = datetime.fromtimestamp(
                        p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and isinstance(
                            data.get("messages"), list):
                        n = self._count_conversation(data)
                        updated = data.get("updated_at", updated) or updated
                except (json.JSONDecodeError, OSError, ValueError):
                    pass  # 条数未知，仍列出文件名供人工检查
                entries.append({
                    "name": p.name,
                    "n_messages": n,
                    "updated_at": updated,
                    "is_current": p.name == "current.json",
                })
        return entries
