"""Unpack containers (ZIP, EML, MSG) into their member files, with zip-bomb protection.

This module only extracts the bytes. The pipeline turns each member into a child Document
and calls us again for nested containers, sharing one Budget so the limits apply to the
whole tree, not just to one level.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import PurePosixPath

from doctordoc.config import IntakeLimits


@dataclass
class Member:
    name: str
    data: bytes


@dataclass
class Budget:
    """Counts what we have unpacked so far across the whole container tree."""

    limits: IntakeLimits
    files: int = 0
    total_bytes: int = 0
    warnings: list[str] = field(default_factory=list)

    def allow_depth(self, depth: int) -> bool:
        return depth <= self.limits.max_depth

    def take(self, name: str, size: int) -> bool:
        """Reserve room for one more file. Returns False (and records why) if over a limit."""
        if self.files + 1 > self.limits.max_files:
            self._warn_once(f"File limit reached ({self.limits.max_files}): the rest was skipped.")
            return False
        if size > self.limits.max_member_mb * 1024 * 1024:
            self.warnings.append(f"Skipped '{name}': larger than {self.limits.max_member_mb} MB.")
            return False
        if self.total_bytes + size > self.limits.max_total_mb * 1024 * 1024:
            self._warn_once(
                f"Size limit reached ({self.limits.max_total_mb} MB): the rest was skipped."
            )
            return False
        self.files += 1
        self.total_bytes += size
        return True

    def _warn_once(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


def extract_members(data: bytes, file_type: str, budget: Budget) -> list[Member]:
    """Return the files inside a container. Unknown container types give an empty list."""
    if file_type == "zip":
        return _from_zip(data, budget)
    if file_type == "eml":
        return _from_eml(data, budget)
    if file_type == "msg":
        return _from_msg(data, budget)
    return []


def safe_name(name: str) -> str:
    """Keep only the last part of a path and drop odd characters. We never write to disk."""
    base = PurePosixPath(name.replace("\\", "/")).name or "unnamed"
    return re.sub(r"[^\w.\- ()]", "_", base)[:120]


def _from_zip(data: bytes, budget: Budget) -> list[Member]:
    members: list[Member] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        budget.warnings.append("ZIP is damaged: nothing unpacked.")
        return members
    with zf:
        for info in zf.infolist():
            if info.is_dir() or info.filename.startswith("__MACOSX/"):
                continue
            if info.flag_bits & 0x1:
                budget.warnings.append(f"Skipped '{info.filename}': encrypted.")
                continue
            # The sizes in the ZIP header can lie, so we check them first (cheap) AND
            # then read with a hard cap (safe).
            if not budget.take(info.filename, info.file_size):
                continue
            cap = int(budget.limits.max_member_mb * 1024 * 1024)
            try:
                with zf.open(info) as handle:
                    content = handle.read(cap + 1)
            except (RuntimeError, zipfile.BadZipFile, NotImplementedError) as err:
                budget.warnings.append(f"Skipped '{info.filename}': {err}")
                continue
            if len(content) > cap:
                budget.warnings.append(f"Skipped '{info.filename}': expands beyond the size limit.")
                continue
            budget.total_bytes += max(0, len(content) - info.file_size)
            members.append(Member(name=info.filename, data=content))
    return members


def _from_eml(data: bytes, budget: Budget) -> list[Member]:
    message = BytesParser(policy=policy.default).parsebytes(data)
    members: list[Member] = []
    for index, part in enumerate(message.iter_attachments(), start=1):
        content = part.get_content()
        if isinstance(content, str):
            content = content.encode(part.get_content_charset() or "utf-8", errors="replace")
        elif not isinstance(content, bytes):  # an attached email (message/rfc822)
            content = bytes(content)
        name = part.get_filename() or f"attachment-{index}"
        if budget.take(name, len(content)):
            members.append(Member(name=name, data=content))
    return members


def _from_msg(data: bytes, budget: Budget) -> list[Member]:
    """Outlook .msg attachments, read with olefile (BSD). UNTESTED: needs a real .msg sample."""
    try:
        import olefile
    except ImportError:
        budget.warnings.append("MSG attachments skipped: install 'olefile' to read them.")
        return []
    members: list[Member] = []
    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except OSError:
        budget.warnings.append("MSG file could not be opened.")
        return members
    with ole:
        for entry in ole.listdir(streams=False, storages=True):
            if len(entry) != 1 or not entry[0].startswith("__attach_version1.0_#"):
                continue
            prefix = entry[0]
            payload = _msg_stream(ole, prefix, "37010102")  # attachment data (binary)
            if payload is None:
                continue
            raw_name = _msg_stream(ole, prefix, "3707001F") or _msg_stream(ole, prefix, "3704001F")
            name = raw_name.decode("utf-16-le", errors="replace") if raw_name else "attachment"
            if budget.take(name, len(payload)):
                members.append(Member(name=name, data=payload))
    return members


def _msg_stream(ole, storage: str, tag: str) -> bytes | None:
    path = f"{storage}/__substg1.0_{tag}"
    return ole.openstream(path).read() if ole.exists(path) else None
