"""Static analysis of Hikka module sources uploaded to the HikkaNet catalog.

The server never imports or executes uploaded code. It only parses the source
with ``ast`` to read metadata and to flag risky constructs, so that a user can
see what a module touches before installing it.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional


CATALOG_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
_META_RE = re.compile(r"^#\s*meta\s+([a-z_]+)\s*:\s*(.+?)\s*$", re.IGNORECASE)

# Codes are stable API values; clients translate them for display.
WARNING_CODES = (
    "dynamic_code",
    "shell",
    "session_access",
    "account_danger",
    "low_level",
    "filesystem",
    "network",
)
_DYNAMIC_CALLS = {"eval", "exec", "compile", "__import__"}
_SHELL_ATTRS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execl"),
    ("os", "spawnl"),
    ("asyncio", "create_subprocess_shell"),
    ("asyncio", "create_subprocess_exec"),
}
_FS_ATTRS = {
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("os", "removedirs"),
    ("shutil", "rmtree"),
    ("shutil", "move"),
}
_LOW_LEVEL_MODULES = {"ctypes", "marshal", "pickle", "dill", "cffi"}
_NETWORK_MODULES = {"aiohttp", "requests", "httpx", "urllib", "socket", "websockets"}
_SESSION_NAMES = {
    "StringSession",
    "SQLiteSession",
    "auth_key",
    "export_session_string",
    "session_string",
}
_ACCOUNT_DANGER = {
    "DeleteAccountRequest",
    "ResetAuthorizationRequest",
    "ResetAuthorizationsRequest",
    "GetAuthorizationsRequest",
    "UpdatePasswordSettingsRequest",
    "GetPasswordRequest",
    "ExportLoginTokenRequest",
    "AcceptLoginTokenRequest",
    "SendCodeRequest",
}


class SourceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ModuleInfo:
    module_id: str
    class_name: str
    title: str
    version: Optional[str]
    description: str
    developer: str
    commands: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sha256: str = ""
    size: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "class_name": self.class_name,
            "title": self.title,
            "version": self.version,
            "description": self.description,
            "developer": self.developer,
            "commands": list(self.commands),
            "warnings": list(self.warnings),
            "sha256": self.sha256,
            "size": self.size,
        }


def module_id_for(class_name: str) -> str:
    """HikkaNetMod -> hikkanet, Weather -> weather."""
    value = class_name.lower()
    if value.endswith("mod") and len(value) > 5:
        value = value[:-3]
    return value


def version_tuple(value: str) -> tuple[int, int, int, int]:
    parts = tuple(int(item) for item in value.split("."))
    return parts + (0,) * (4 - len(parts))


def _clean_line(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _is_module_base(base: ast.expr) -> bool:
    return (isinstance(base, ast.Attribute) and base.attr == "Module") or (
        isinstance(base, ast.Name) and base.id == "Module"
    )


def _meta(source: str) -> dict[str, str]:
    result = {}
    for line in source.splitlines()[:80]:
        match = _META_RE.match(line.strip())
        if match:
            result.setdefault(match.group(1).lower(), match.group(2))
    return result


def _literal_version(node: ast.AST) -> Optional[str]:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if isinstance(value, (tuple, list)) and 2 <= len(value) <= 4:
        if all(isinstance(item, int) and 0 <= item < 100000 for item in value):
            return ".".join(str(item) for item in value)
    if isinstance(value, str) and VERSION_RE.fullmatch(value.strip()):
        return value.strip()
    return None


def _module_version(tree: ast.Module, meta: dict[str, str]) -> Optional[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            version = _literal_version(node.value)
            if version:
                return version
    raw = meta.get("version", "").strip().lstrip("vV")
    return raw if VERSION_RE.fullmatch(raw) else None


def _class_title(cls: ast.ClassDef) -> str:
    for node in cls.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "strings" for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "name"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                return _clean_line(value.value, 64)
    return ""


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _commands(cls: ast.ClassDef) -> list[str]:
    result = []
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = {_decorator_name(item) for item in node.decorator_list}
        if "command" in decorators:
            name = node.name[:-3] if node.name.endswith("cmd") else node.name
        elif node.name.endswith("cmd") and len(node.name) > 3:
            name = node.name[:-3]
        else:
            continue
        if name and name not in result:
            result.append(name)
    return result[:60]


def _dotted(node: ast.expr) -> tuple[str, ...]:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def scan_warnings(tree: ast.AST) -> list[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                root = name.split(".")[0]
                if root == "subprocess" or root == "pty":
                    found.add("shell")
                elif root in _LOW_LEVEL_MODULES:
                    found.add("low_level")
                elif root in _NETWORK_MODULES:
                    found.add("network")
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in _SESSION_NAMES:
                        found.add("session_access")
                    elif alias.name in _ACCOUNT_DANGER:
                        found.add("account_danger")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _DYNAMIC_CALLS:
                found.add("dynamic_code")
            dotted = _dotted(func)
            if len(dotted) >= 2:
                pair = dotted[-2:]
                if pair in _SHELL_ATTRS:
                    found.add("shell")
                elif pair in _FS_ATTRS:
                    found.add("filesystem")
                if dotted[-1] in _DYNAMIC_CALLS and dotted[0] in {"builtins", "__builtins__"}:
                    found.add("dynamic_code")
        elif isinstance(node, ast.Attribute):
            if node.attr in _SESSION_NAMES or (
                node.attr == "save" and _dotted(node.value)[-1:] == ("session",)
            ):
                found.add("session_access")
            elif node.attr in _ACCOUNT_DANGER:
                found.add("account_danger")
        elif isinstance(node, ast.Name):
            if node.id in _SESSION_NAMES:
                found.add("session_access")
            elif node.id in _ACCOUNT_DANGER:
                found.add("account_danger")
    return [code for code in WARNING_CODES if code in found]


def analyse_source(source: Any, max_bytes: int) -> ModuleInfo:
    """Parse an uploaded module without executing it and return its metadata."""
    if not isinstance(source, str) or not source.strip():
        raise SourceError("invalid_source", "source must be a non-empty string")
    if "\x00" in source:
        raise SourceError("invalid_source", "source cannot contain NUL bytes")
    encoded = source.encode("utf-8")
    if len(encoded) > max_bytes:
        raise SourceError("source_too_large", f"Module source exceeds {max_bytes} bytes")
    try:
        tree = ast.parse(source, filename="<catalog>")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        line = getattr(exc, "lineno", None)
        where = f" (line {line})" if line else ""
        raise SourceError("syntax_error", f"Module has a syntax error{where}") from None

    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and any(_is_module_base(b) for b in node.bases)
    ]
    if not classes:
        raise SourceError(
            "not_a_module", "Source does not define a Hikka loader.Module class"
        )
    if len(classes) > 1:
        raise SourceError("multiple_modules", "Upload one Hikka module per file")
    cls = classes[0]
    module_id = module_id_for(cls.name)
    if not CATALOG_ID_RE.fullmatch(module_id):
        raise SourceError(
            "invalid_module_name",
            "Module class name must be 2..48 Latin letters, digits or _",
        )

    meta = _meta(source)
    description = _clean_line(meta.get("description") or ast.get_docstring(cls) or "", 300)
    return ModuleInfo(
        module_id=module_id,
        class_name=cls.name,
        title=_class_title(cls) or cls.name,
        version=_module_version(tree, meta),
        description=description,
        developer=_clean_line(meta.get("developer", ""), 64),
        commands=_commands(cls),
        warnings=scan_warnings(tree),
        sha256=hashlib.sha256(encoded).hexdigest(),
        size=len(encoded),
    )


def clean_tags(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    if not isinstance(raw, list) or len(raw) > 8:
        raise SourceError("invalid_tags", "tags must be a list of at most 8 items")
    result = []
    for item in raw:
        tag = str(item).strip().lower().lstrip("#")
        if not TAG_RE.fullmatch(tag):
            raise SourceError("invalid_tags", "Tags use a-z, 0-9, _ or -, up to 24 chars")
        if tag not in result:
            result.append(tag)
    return result
