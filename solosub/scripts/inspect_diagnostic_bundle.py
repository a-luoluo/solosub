#!/usr/bin/env python3
"""Safely inventory a Soloco diagnostic file or ZIP and mask credentials.

The script is read-only: it never executes archive content and never extracts files.
It emits JSON to stdout for both human review and downstream Agent analysis.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import stat
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


TEXT_EXTENSIONS = {
    ".txt",
    ".log",
    ".json",
    ".jsonl",
    ".ndjson",
    ".md",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".xml",
    ".html",
}

NOTABLE_RE = re.compile(
    r"(?:error|exception|failed|failure|fatal|panic|timeout|timed out|blocked|"
    r"denied|invalid|conflict|crash|goal\.planning_failed|runtime_schema|"
    r"错误|异常|失败|超时|阻断|拒绝|冲突|崩溃)",
    re.IGNORECASE,
)

MOJIBAKE_RE = re.compile(r"(?:锟斤拷|�|Ã.|Â.|â€™|â€œ|â€|æ—|ä¸|ä¸­æ–‡)")

REDACTION_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "bearer_token",
        re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
        r"\1 <redacted>",
    ),
    (
        "sensitive_key",
        re.compile(
            r"(?i)(\b(?:access[_-]?token|refresh[_-]?token|api[_-]?key|app[_-]?secret|"
            r"client[_-]?secret|password|passwd|cookie|authorization|session[_-]?id)\b\s*[:=]\s*)"
            r"([^\s,;\]\}\"']+|\"[^\"]*\"|'[^']*')"
        ),
        r"\1<redacted>",
    ),
    (
        "url_secret",
        re.compile(
            r"(?i)([?&](?:token|access_token|code|key|secret|signature|sig)=)[^&#\s]+"
        ),
        r"\1<redacted>",
    ),
]


@dataclass
class Limits:
    max_members: int
    max_total_uncompressed: int
    max_member_bytes: int
    max_text_bytes: int
    max_excerpts: int
    max_line_chars: int = 1200


def sha256_stream(stream: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def decode_text(data: bytes) -> tuple[str, str, bool]:
    candidates: list[tuple[str, str]] = []
    if data.startswith(b"\xef\xbb\xbf"):
        candidates.append(("utf-8-sig", "utf-8-sig"))
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates.append(("utf-16", "utf-16"))
    candidates.extend(
        [
            ("utf-8", "utf-8"),
            ("gb18030", "gb18030"),
            ("utf-16", "utf-16"),
        ]
    )
    tried: set[str] = set()
    for label, codec in candidates:
        if codec in tried:
            continue
        tried.add(codec)
        try:
            return data.decode(codec), label, False
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replacement", True


def redact(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    redacted = text
    for name, pattern, replacement in REDACTION_RULES:
        redacted, count = pattern.subn(replacement, redacted)
        if count:
            counts[name] = counts.get(name, 0) + count
    return redacted, counts


def merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value


def is_safe_member(info: zipfile.ZipInfo) -> tuple[bool, str | None]:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if not name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return False, "absolute_path"
    if ".." in path.parts:
        return False, "parent_traversal"
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if unix_mode and stat.S_ISLNK(unix_mode):
        return False, "symlink"
    return True, None


def text_like(name: str) -> bool:
    return Path(name).suffix.lower() in TEXT_EXTENSIONS


def analyze_text(
    source_name: str,
    data: bytes,
    limits: Limits,
    excerpt_budget: int,
) -> tuple[dict[str, object], list[dict[str, object]], dict[str, int]]:
    truncated = len(data) > limits.max_text_bytes
    sample = data[: limits.max_text_bytes]
    decoded, encoding, had_decode_errors = decode_text(sample)
    redacted, redaction_counts = redact(decoded)
    excerpts: list[dict[str, object]] = []
    mojibake_hits = 0
    for line_number, line in enumerate(redacted.splitlines(), start=1):
        if MOJIBAKE_RE.search(line):
            mojibake_hits += 1
        if len(excerpts) >= excerpt_budget:
            continue
        if NOTABLE_RE.search(line):
            excerpts.append(
                {
                    "source": source_name,
                    "line": line_number,
                    "text": line[: limits.max_line_chars],
                }
            )
    analysis = {
        "encoding": encoding,
        "decode_errors": had_decode_errors,
        "text_bytes_scanned": len(sample),
        "truncated": truncated,
        "line_count_scanned": redacted.count("\n") + (1 if redacted else 0),
        "notable_lines": len(excerpts),
        "mojibake_lines": mojibake_hits,
    }
    return analysis, excerpts, redaction_counts


def inspect_regular_file(path: Path, limits: Limits) -> dict[str, object]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        source_hash = sha256_stream(stream)
    result: dict[str, object] = {
        "schema_version": 1,
        "source": {
            "name": path.name,
            "kind": "file",
            "size_bytes": size,
            "sha256": source_hash,
        },
        "members": [],
        "notable_excerpts": [],
        "redactions": {},
        "warnings": [],
    }
    member: dict[str, object] = {
        "name": path.name,
        "size_bytes": size,
        "safe": True,
        "text": text_like(path.name),
    }
    if text_like(path.name):
        with path.open("rb") as stream:
            data = stream.read(limits.max_text_bytes + 1)
        analysis, excerpts, counts = analyze_text(
            path.name, data, limits, limits.max_excerpts
        )
        member["analysis"] = analysis
        result["notable_excerpts"] = excerpts
        result["redactions"] = counts
    else:
        result["warnings"] = [
            "Binary input was inventoried but not parsed. Provide text/JSON logs for diagnosis."
        ]
    result["members"] = [member]
    return result


def inspect_stdin(data: bytes, limits: Limits) -> dict[str, object]:
    digest = hashlib.sha256(data).hexdigest()
    analysis, excerpts, counts = analyze_text(
        "stdin.txt", data, limits, limits.max_excerpts
    )
    return {
        "schema_version": 1,
        "source": {
            "name": "stdin.txt",
            "kind": "stdin",
            "size_bytes": len(data),
            "sha256": digest,
        },
        "members": [
            {
                "name": "stdin.txt",
                "size_bytes": len(data),
                "safe": True,
                "text": True,
                "analysis": analysis,
            }
        ],
        "notable_excerpts": excerpts,
        "redactions": counts,
        "warnings": [],
    }


def inspect_zip(path: Path, limits: Limits) -> dict[str, object]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        source_hash = sha256_stream(stream)
    result: dict[str, object] = {
        "schema_version": 1,
        "source": {
            "name": path.name,
            "kind": "zip",
            "size_bytes": size,
            "sha256": source_hash,
        },
        "members": [],
        "notable_excerpts": [],
        "redactions": {},
        "warnings": [],
    }
    members: list[dict[str, object]] = []
    excerpts: list[dict[str, object]] = []
    redaction_counts: dict[str, int] = {}
    warnings: list[str] = []
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > limits.max_members:
            raise ValueError(
                f"archive has {len(infos)} members; limit is {limits.max_members}"
            )
        total_uncompressed = sum(info.file_size for info in infos)
        if total_uncompressed > limits.max_total_uncompressed:
            raise ValueError(
                "archive uncompressed size exceeds safety limit: "
                f"{total_uncompressed} > {limits.max_total_uncompressed}"
            )
        text_budget = limits.max_text_bytes
        for info in infos:
            safe, reason = is_safe_member(info)
            member: dict[str, object] = {
                "name": info.filename,
                "size_bytes": info.file_size,
                "compressed_bytes": info.compress_size,
                "safe": safe,
                "text": text_like(info.filename),
            }
            if not safe:
                member["rejected_reason"] = reason
                warnings.append(f"rejected unsafe ZIP member: {info.filename} ({reason})")
                members.append(member)
                continue
            if info.is_dir():
                member["kind"] = "directory"
                members.append(member)
                continue
            if info.file_size > limits.max_member_bytes:
                member["analysis_skipped"] = "member_too_large"
                warnings.append(f"skipped oversized member: {info.filename}")
                members.append(member)
                continue
            if text_like(info.filename) and text_budget > 0:
                read_limit = min(info.file_size, text_budget, limits.max_member_bytes)
                with archive.open(info, "r") as stream:
                    data = stream.read(read_limit + 1)
                remaining_excerpts = max(0, limits.max_excerpts - len(excerpts))
                analysis, found, counts = analyze_text(
                    info.filename, data, limits, remaining_excerpts
                )
                member["analysis"] = analysis
                excerpts.extend(found)
                merge_counts(redaction_counts, counts)
                text_budget -= min(len(data), read_limit)
            members.append(member)
    result["members"] = members
    result["notable_excerpts"] = excerpts
    result["redactions"] = redaction_counts
    result["warnings"] = warnings
    result["summary"] = {
        "member_count": len(members),
        "safe_member_count": sum(1 for item in members if item.get("safe")),
        "rejected_member_count": sum(1 for item in members if not item.get("safe")),
        "notable_excerpt_count": len(excerpts),
        "redaction_count": sum(redaction_counts.values()),
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely inventory a Soloco diagnostic file or ZIP and mask credentials."
    )
    parser.add_argument("path", help="Path to a file/ZIP, or - to read text from stdin")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--max-members", type=int, default=500)
    parser.add_argument("--max-total-uncompressed", type=int, default=200 * 1024 * 1024)
    parser.add_argument("--max-member-bytes", type=int, default=25 * 1024 * 1024)
    parser.add_argument("--max-text-bytes", type=int, default=20 * 1024 * 1024)
    parser.add_argument("--max-excerpts", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    limits = Limits(
        max_members=args.max_members,
        max_total_uncompressed=args.max_total_uncompressed,
        max_member_bytes=args.max_member_bytes,
        max_text_bytes=args.max_text_bytes,
        max_excerpts=args.max_excerpts,
    )
    try:
        if args.path == "-":
            result = inspect_stdin(sys.stdin.buffer.read(limits.max_text_bytes + 1), limits)
        else:
            path = Path(args.path)
            if not path.exists():
                raise FileNotFoundError(f"diagnostic path does not exist: {path}")
            if not path.is_file():
                raise ValueError("provide one diagnostic file or ZIP archive")
            if zipfile.is_zipfile(path):
                result = inspect_zip(path, limits)
            else:
                result = inspect_regular_file(path, limits)
        indent = 2 if args.pretty else None
        json.dump(result, sys.stdout, ensure_ascii=False, indent=indent)
        sys.stdout.write("\n")
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        json.dump(
            {
                "schema_version": 1,
                "ok": False,
                "error": {"type": exc.__class__.__name__, "message": str(exc)},
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
