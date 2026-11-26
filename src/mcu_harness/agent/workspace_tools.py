"""工作区安全文件操作与 Shell 执行工具集（纯 Python 标准库）。

安全约束：
    - `_resolve_workspace_path()` 强制所有读写/列表路径在解析符号链接后仍位于 `project_dir` 内部，
      任何越界（如 `../../etc/passwd` 或工作区外绝对路径）一律抛出 `ValueError`。
    - 二进制文件保护：读取或搜索时自动跳过/拒绝包含 NUL 字节的二进制文件。
    - 精确编辑校验：`preview_edit_diff()` 与 `tool_edit_file()` 仅在 `old_text` 唯一匹配 1 次时生效，
      找不到或存在多处匹配时明确报错，避免误改源码。
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import subprocess
from pathlib import Path
from typing import Any

_SKIP_DIRS: frozenset[str] = frozenset({".git", "build", "__pycache__", ".venv", "node_modules"})
_MAX_SHELL_OUTPUT_CHARS = 16000


def _resolve_workspace_path(project_dir: str | os.PathLike, raw_path: str) -> Path:
    """将工作区相对或绝对路径解析为安全规范路径，越界时抛出 `ValueError`。"""
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("path must be a non-empty string")
    root = Path(project_dir).expanduser().resolve()
    cand = Path(raw_path.strip())
    resolved = cand.resolve() if cand.is_absolute() else (root / cand).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace root ({root}): {raw_path!r}") from exc
    return resolved


def _rel_display(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root).as_posix()
        return rel if rel else "."
    except ValueError:
        return str(path)


def _is_binary_bytes(sample: bytes) -> bool:
    return b"\x00" in sample[:8192]


def _truncate_output(text: str, limit: int = _MAX_SHELL_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit // 2
    tail = limit - head
    omitted = len(text) - limit
    return f"{text[:head]}\n... [truncated {omitted} chars] ...\n{text[-tail:]}", True


def tool_read_file(
    project_dir: str,
    path: str,
    start_line: int = 1,
    end_line: int = 200,
) -> dict[str, Any]:
    """按行范围读取工作区文本文件，输出带行号内容并保护二进制/越界访问。"""
    root = Path(project_dir).expanduser().resolve()
    target = _resolve_workspace_path(root, path)
    rel = _rel_display(target, root)

    if not target.exists():
        return {"error": f"File not found: {rel}"}
    if not target.is_file():
        return {"error": f"Not a regular file: {rel}"}
    if start_line < 1:
        return {"error": f"start_line must be >= 1 (got {start_line})"}
    if end_line < start_line:
        return {"error": f"end_line ({end_line}) must be >= start_line ({start_line})"}

    raw_bytes = target.read_bytes()
    if _is_binary_bytes(raw_bytes):
        return {"error": f"Refusing to read binary file: {rel}"}

    text = raw_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    total_lines = len(lines)

    if total_lines == 0:
        return {
            "path": rel,
            "start_line": 1,
            "end_line": 0,
            "total_lines": 0,
            "truncated": False,
            "content": "",
        }

    if start_line > total_lines:
        return {
            "path": rel,
            "start_line": start_line,
            "end_line": total_lines,
            "total_lines": total_lines,
            "truncated": False,
            "content": "",
        }

    actual_end = min(end_line, total_lines)
    selected = lines[start_line - 1 : actual_end]
    numbered = "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=start_line))
    return {
        "path": rel,
        "start_line": start_line,
        "end_line": actual_end,
        "total_lines": total_lines,
        "truncated": actual_end < total_lines,
        "content": numbered,
    }


def _compute_single_edit(
    project_dir: str,
    path: str,
    old_text: str,
    new_text: str,
) -> tuple[Path, str, str, str]:
    root = Path(project_dir).expanduser().resolve()
    target = _resolve_workspace_path(root, path)
    rel = _rel_display(target, root)

    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"File not found: {rel}")
    if not old_text:
        raise ValueError("old_text must be non-empty")

    raw_bytes = target.read_bytes()
    if _is_binary_bytes(raw_bytes):
        raise ValueError(f"Refusing to edit binary file: {rel}")

    original = raw_bytes.decode("utf-8")
    count = original.count(old_text)
    if count == 0:
        raise ValueError(f"old_text not found in {rel}")
    if count > 1:
        raise ValueError(f"old_text matched {count} occurrences in {rel}; provide more context for a unique match")

    updated = original.replace(old_text, new_text, 1)
    diff_lines = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
    )
    diff_text = "".join(diff_lines)
    return target, rel, updated, diff_text


def preview_edit_diff(
    project_dir: str,
    path: str,
    old_text: str,
    new_text: str,
) -> str:
    """校验单处精确替换并返回 unified diff 预览字符串（不落盘）。"""
    _, _, _, diff_text = _compute_single_edit(project_dir, path, old_text, new_text)
    return diff_text


def tool_edit_file(
    project_dir: str,
    path: str,
    old_text: str,
    new_text: str,
) -> dict[str, Any]:
    """对工作区文本文件执行唯一精确匹配替换并写盘，返回 unified diff 摘要。"""
    target = _resolve_workspace_path(project_dir, path)
    try:
        resolved_target, rel, updated, diff_text = _compute_single_edit(project_dir, path, old_text, new_text)
    except (FileNotFoundError, ValueError, UnicodeDecodeError) as exc:
        return {"success": False, "path": str(target), "error": str(exc)}

    resolved_target.write_text(updated, encoding="utf-8")
    return {
        "success": True,
        "path": rel,
        "diff": diff_text,
    }


def tool_write_file(
    project_dir: str,
    path: str,
    content: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """在工作区内创建或覆写文件（自动创建父目录；默认拒绝覆盖已存在文件）。"""
    root = Path(project_dir).expanduser().resolve()
    target = _resolve_workspace_path(root, path)
    rel = _rel_display(target, root)

    if target.exists() and not overwrite:
        return {
            "success": False,
            "path": rel,
            "error": f"File already exists: {rel} (pass overwrite=True to replace)",
        }
    if target.is_dir():
        return {"success": False, "path": rel, "error": f"Target is a directory: {rel}"}

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "success": True,
        "path": rel,
        "bytes_written": len(content.encode("utf-8")),
    }


def tool_list_files(
    project_dir: str,
    path: str = ".",
    max_depth: int = 3,
    max_entries: int = 200,
) -> dict[str, Any]:
    """列出工作区目录树（跳过 `.git`/`build`/`__pycache__`/`.venv`/`node_modules`）。"""
    root = Path(project_dir).expanduser().resolve()
    base = _resolve_workspace_path(root, path)
    rel_base = _rel_display(base, root)

    if not base.exists():
        return {"error": f"Directory not found: {rel_base}"}
    if not base.is_dir():
        return {"error": f"Not a directory: {rel_base}"}
    if max_depth < 1:
        return {"error": f"max_depth must be >= 1 (got {max_depth})"}
    if max_entries < 1:
        return {"error": f"max_entries must be >= 1 (got {max_entries})"}

    entries: list[dict[str, Any]] = []
    truncated = False

    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        cur = Path(dirpath)
        depth = len(cur.relative_to(base).parts)
        if depth >= max_depth:
            dirnames.clear()

        for dname in list(dirnames):
            dpath = cur / dname
            entries.append({"path": _rel_display(dpath, root) + "/", "type": "dir"})
            if len(entries) >= max_entries:
                truncated = True
                break
        if truncated:
            break

        for fname in sorted(filenames):
            fpath = cur / fname
            try:
                size = fpath.stat().st_size
            except OSError:
                size = None
            entries.append({"path": _rel_display(fpath, root), "type": "file", "size": size})
            if len(entries) >= max_entries:
                truncated = True
                break
        if truncated:
            break

    return {
        "root": rel_base,
        "count": len(entries),
        "truncated": truncated,
        "entries": entries,
    }


def tool_search_files(
    project_dir: str,
    pattern: str,
    file_glob: str = "*",
    max_results: int = 50,
) -> dict[str, Any]:
    """在工作区文本文件中按正则检索匹配行，返回 `file:line: text` 列表。"""
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        return {"error": f"Project directory does not exist: {root}"}
    if max_results < 1:
        return {"error": f"max_results must be >= 1 (got {max_results})"}
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return {"error": f"Invalid regex pattern {pattern!r}: {exc}"}

    matches: list[dict[str, Any]] = []
    lines_out: list[str] = []
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        cur = Path(dirpath)
        for fname in sorted(filenames):
            fpath = cur / fname
            rel = _rel_display(fpath, root)
            if file_glob and not (fnmatch.fnmatch(fname, file_glob) or fnmatch.fnmatch(rel, file_glob)):
                continue
            try:
                raw_bytes = fpath.read_bytes()
            except OSError:
                continue
            if _is_binary_bytes(raw_bytes):
                continue
            text = raw_bytes.decode("utf-8", errors="replace")
            for lineno, line_text in enumerate(text.splitlines(), start=1):
                if regex.search(line_text):
                    formatted = f"{rel}:{lineno}: {line_text}"
                    matches.append(
                        {
                            "file": rel,
                            "line": lineno,
                            "text": line_text,
                            "formatted": formatted,
                        }
                    )
                    lines_out.append(formatted)
                    if len(matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break

    return {
        "pattern": pattern,
        "file_glob": file_glob,
        "count": len(matches),
        "truncated": truncated,
        "lines": lines_out,
        "matches": matches,
    }


def tool_run_shell(
    project_dir: str,
    command: str,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """在 `project_dir` 下执行 Shell 命令，安全截断超长输出并返回状态码与超时标记。"""
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        return {"error": f"Project directory does not exist: {root}"}
    if not command or not command.strip():
        return {"error": "command must be a non-empty string"}

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        stdout, out_trunc = _truncate_output(proc.stdout or "")
        stderr, err_trunc = _truncate_output(proc.stderr or "")
        return {
            "command": command,
            "cwd": str(root),
            "return_code": proc.returncode,
            "timed_out": False,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": out_trunc or err_trunc,
        }
    except subprocess.TimeoutExpired as exc:
        raw_out = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        raw_err = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        stdout, out_trunc = _truncate_output(raw_out)
        stderr, err_trunc = _truncate_output(raw_err)
        return {
            "command": command,
            "cwd": str(root),
            "return_code": -1,
            "timed_out": True,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": out_trunc or err_trunc,
            "error": f"Command timed out after {timeout_s}s",
        }
    except OSError as exc:
        return {
            "command": command,
            "cwd": str(root),
            "return_code": -1,
            "timed_out": False,
            "stdout": "",
            "stderr": str(exc),
            "truncated": False,
            "error": str(exc),
        }
