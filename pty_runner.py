import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agy_bot_core import build_safe_subprocess_env, redact_sensitive, run_process


logger = logging.getLogger(__name__)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\([a-zA-Z]|\x1b\][^\x07]*\x07")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def format_quota_limit_line(
    name: str,
    used: float,
    total: float,
    unit: str = "",
    cycle_elapsed_ratio: float | None = None,
) -> str:
    if total <= 0:
        return f"• <b>{name}</b>: {used:.1f} {unit} ⚪ <b>[無上限/未知]</b>".strip()

    usage_pct = (used / total) * 100.0
    remaining_pct = max(0.0, 100.0 - usage_pct)

    if cycle_elapsed_ratio is not None and 0.0 <= cycle_elapsed_ratio <= 1.0:
        time_pct = cycle_elapsed_ratio * 100.0
        delta = time_pct - usage_pct  # positive = surplus/ahead
        if delta > 15:
            indicator = f"⭐ <b>[+{delta:.0f}%]</b>"
        elif delta >= -5:
            indicator = "🟢 <b>[On track]</b>"
        elif delta >= -20:
            indicator = f"🟡 <b>[{delta:.0f}%]</b>"
        else:
            indicator = f"🔴 <b>[{delta:.0f}%]</b>"
    else:
        if remaining_pct > 50:
            indicator = "🟢"
        elif remaining_pct > 20:
            indicator = "🟡"
        else:
            indicator = "🔴"

    return f"• <b>{name}</b>: {used:.1f}/{total:.1f} {unit} ({remaining_pct:.1f}% 剩餘) {indicator}".strip()


def format_structured_quota(data: dict[str, Any]) -> str:
    lines = ["📊 <b>AGY 使用量與配額資訊</b>\n"]
    if "user" in data or "account" in data:
        account = data.get("user") or data.get("account")
        lines.append(f"👤 帳號：<code>{account}</code>")

    # Check limits or quotas
    quotas = data.get("quotas") or data.get("limits") or data.get("buckets")
    if isinstance(quotas, list):
        lines.append("\n<b>配額項目：</b>")
        for q in quotas:
            if isinstance(q, dict):
                name = q.get("name") or q.get("model") or "預設配額"
                used = float(q.get("used", 0))
                total = float(q.get("total", q.get("limit", 0)))
                unit = q.get("unit", "")
                cycle_ratio = q.get("cycle_elapsed_ratio")
                if cycle_ratio is None and "cycle_start" in q and "cycle_end" in q:
                    try:
                        t_start = datetime.fromisoformat(q["cycle_start"]).timestamp()
                        t_end = datetime.fromisoformat(q["cycle_end"]).timestamp()
                        now_ts = datetime.now(timezone.utc).timestamp()
                        if t_end > t_start:
                            cycle_ratio = max(0.0, min(1.0, (now_ts - t_start) / (t_end - t_start)))
                    except Exception:
                        pass
                lines.append(format_quota_limit_line(name, used, total, unit, cycle_ratio))
    elif isinstance(quotas, dict):
        lines.append("\n<b>配額項目：</b>")
        for name, q in quotas.items():
            if isinstance(q, dict):
                used = float(q.get("used", 0))
                total = float(q.get("total", q.get("limit", 0)))
                unit = q.get("unit", "")
                lines.append(format_quota_limit_line(name, used, total, unit))
            else:
                lines.append(f"• <b>{name}</b>: {q}")

    if "reset_time" in data or "resets_in" in data:
        reset = data.get("reset_time") or data.get("resets_in")
        lines.append(f"\n⏳ 重置時間：<code>{reset}</code>")

    if len(lines) == 1:
        # Generic dict dump
        for k, v in data.items():
            lines.append(f"• <b>{k}</b>: {v}")

    return "\n".join(lines)


def format_context_report(raw_text: str) -> str:
    clean = strip_ansi(raw_text).strip()
    if not clean:
        return "⚠️ 上下文資訊為空。"

    # Check if raw_text is JSON
    if clean.startswith("{") and clean.endswith("}"):
        try:
            data = json.loads(clean)
            lines = ["🧠 <b>AGY 上下文結構化明細</b>\n"]
            if "model" in data:
                lines.append(f"• <b>模型</b>: <code>{data['model']}</code>")
            if "tokens" in data or "usage" in data:
                t = data.get("tokens") or data.get("usage")
                if isinstance(t, dict):
                    lines.append("\n📊 <b>Token 使用分類：</b>")
                    for k, v in t.items():
                        lines.append(f"  - <b>{k}</b>: <code>{v}</code>")
                else:
                    lines.append(f"• <b>Token 用量</b>: <code>{t}</code>")
            if "checkpoint" in data:
                lines.append(f"• <b>Checkpoint</b>: <code>{data['checkpoint']}</code>")
            if "artifacts" in data:
                lines.append(f"• <b>Artifact 數量</b>: <code>{data['artifacts']}</code>")
            return "\n".join(lines)
        except Exception:
            pass

    lines = ["🧠 <b>AGY 當前對話上下文明細</b>\n"]
    for line in clean.splitlines():
        l_str = line.strip()
        if not l_str:
            continue
        if "token" in l_str.lower() or "usage" in l_str.lower():
            lines.append(f"📊 {l_str}")
        elif "model" in l_str.lower():
            lines.append(f"🤖 {l_str}")
        elif "checkpoint" in l_str.lower():
            lines.append(f"🛡️ {l_str}")
        elif "artifact" in l_str.lower() or "file" in l_str.lower():
            lines.append(f"📁 {l_str}")
        else:
            lines.append(f"• {l_str}")
    return "\n".join(lines)


async def _run_structured_quota(
    agy_bin: Path, workdir: Path, timeout_seconds: float
) -> str | None:
    env = build_safe_subprocess_env(extra_path=agy_bin.parent)
    args = [
        str(agy_bin),
        "--print",
        "/quota",
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    try:
        res = await run_process(
            args,
            cwd=workdir,
            env=env,
            timeout_seconds=timeout_seconds,
            max_output_bytes=200_000,
        )
        if res.returncode == 0 and res.stdout:
            clean = res.stdout.strip()
            if clean.startswith("{") and clean.endswith("}"):
                data = json.loads(clean)
                return format_structured_quota(data)
            elif "quota" in clean.lower() or "limit" in clean.lower():
                return strip_ansi(res.stdout)
    except Exception as exc:
        logger.debug("Structured quota query returned error: %s", exc)
    return None


def _run_pty_sync(
    agy_bin: Path, workdir: Path, slash_command: str, timeout_seconds: float
) -> str:
    import pexpect

    env = build_safe_subprocess_env(extra_path=agy_bin.parent)
    child = None
    try:
        child = pexpect.spawn(
            str(agy_bin),
            cwd=str(workdir),
            env=env,
            encoding="utf-8",
            timeout=timeout_seconds,
        )

        patterns = [
            r"trust this (folder|workspace)",
            r"Are you sure",
            r"Not logged in",
            r"Please sign in",
            pexpect.TIMEOUT,
            pexpect.EOF,
        ]

        # Read initial prompt
        idx = child.expect(patterns, timeout=min(5.0, timeout_seconds))
        if idx == 0 or idx == 1:
            child.sendline("y")
        elif idx in (2, 3):
            return "❌ AGY 尚未登入，請先於終端機執行 `agy` 登入授權。"

        import time
        time.sleep(0.5)
        for ch in slash_command:
            child.send(ch)
            time.sleep(0.01)
        child.sendline()

        output_chunks = []
        end_time = time.time() + timeout_seconds
        while time.time() < end_time:
            try:
                chunk = child.read_nonblocking(size=4096, timeout=1.0)
                if chunk:
                    output_chunks.append(chunk)
            except pexpect.TIMEOUT:
                if output_chunks:
                    break
            except pexpect.EOF:
                break

        full_output = "".join(output_chunks)
        clean = strip_ansi(full_output).strip()
        if slash_command.startswith("/context"):
            return format_context_report(clean)
        return redact_sensitive(clean) if clean else "⚠️ 互動模式未產生輸出。"
    except pexpect.TIMEOUT:
        return "⚠️ 指令執行逾時。"
    except Exception as exc:
        logger.exception("PTY 執行異常：%s", exc)
        return f"❌ PTY 執行異常：{redact_sensitive(str(exc))}"
    finally:
        if child is not None:
            try:
                child.close(force=True)
            except Exception:
                pass


async def run_pty_command(
    agy_bin: Path,
    workdir: Path,
    slash_command: str,
    timeout_seconds: float = 30.0,
) -> str:
    norm_cmd = slash_command.strip()
    if norm_cmd in {"/usage", "/quota", "/credits"}:
        structured = await _run_structured_quota(agy_bin, workdir, timeout_seconds=min(15.0, timeout_seconds))
        if structured:
            return structured

    return await asyncio.to_thread(
        _run_pty_sync, agy_bin, workdir, norm_cmd, timeout_seconds
    )
