"""Plugin & marketplace system for ccb-py.

A pragmatic reimplementation of the official Claude Code plugin model.
We support the three most common source types — which is what 99% of
``/plugin`` and ``/plugin marketplace add`` commands actually use:

    github:      owner/repo         → git clone https://github.com/owner/repo
    url:         https://...        → git clone, or HTTP-GET if .json
    local path:  /abs or ./rel      → read in place

Storage layout (matches the official client so its marketplaces Just Work):

    ~/.claude/plugins/
    ├── known_marketplaces.json          # { name: {source, installLocation} }
    ├── installed_plugins.json           # { "plugin@marketplace": {path, enabled} }
    └── marketplaces/
        └── <name>/                      # cloned marketplace repo
            └── .claude-plugin/
                └── marketplace.json     # { name, owner, plugins: [...] }

A marketplace's ``marketplace.json`` lists plugins:

    {
      "name": "superpowers-marketplace",
      "owner": {"name": "obra"},
      "plugins": [
        {
          "name": "superpowers",
          "description": "...",
          "source": {"source": "github", "repo": "obra/superpowers"}
        }
      ]
    }

Each installed plugin is expected to have ``.claude-plugin/plugin.json`` at
its root. After install we also scan for:

    <plugin_root>/commands/*.md   → exposed as /<name> slash commands
    <plugin_root>/agents/*.md     → exposed as subagent types
    <plugin_root>/hooks/*.json    → wired into the hook system
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ── Paths ────────────────────────────────────────────────────────────

def _plugins_dir() -> Path:
    d = Path.home() / ".claude" / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _marketplaces_cache_dir() -> Path:
    d = _plugins_dir() / "marketplaces"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _known_marketplaces_file() -> Path:
    return _plugins_dir() / "known_marketplaces.json"


def _installed_plugins_file() -> Path:
    return _plugins_dir() / "installed_plugins.json"


# ── Builtin / official marketplace ───────────────────────────────────

OFFICIAL_MARKETPLACE_NAME = "claude-plugins-official"
OFFICIAL_MARKETPLACE_REPO = "anthropics/claude-plugins-official"


# ── Source parsing ───────────────────────────────────────────────────

@dataclass
class MarketplaceSource:
    kind: str          # "github" | "git" | "url" | "directory" | "file"
    value: str         # "owner/repo", URL, or absolute path
    ref: str | None = None    # optional git ref

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"source": self.kind}
        if self.kind == "github":
            d["repo"] = self.value
        elif self.kind == "git":
            d["url"] = self.value
        elif self.kind == "url":
            d["url"] = self.value
        else:  # directory or file
            d["path"] = self.value
        if self.ref:
            d["ref"] = self.ref
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MarketplaceSource:
        kind = d.get("source", "")
        if kind == "github":
            return cls(kind="github", value=d.get("repo", ""), ref=d.get("ref"))
        if kind == "git":
            return cls(kind="git", value=d.get("url", ""), ref=d.get("ref"))
        if kind == "url":
            return cls(kind="url", value=d.get("url", ""))
        if kind in ("directory", "file"):
            return cls(kind=kind, value=d.get("path", ""))
        raise ValueError(f"Unknown source kind: {kind}")


_GITHUB_SHORTHAND_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def parse_marketplace_input(source: str) -> MarketplaceSource:
    """Detect source type from a user-entered string.

    Patterns (checked in order):
      owner/repo          → github
      git@host:...        → git (SSH)
      https://...git      → git
      https://....json    → url
      https://...         → git (assume repo)
      ./path or /path     → directory (if exists & is dir) or file

    Raises ValueError on unrecognized input.
    """
    s = source.strip()
    if not s:
        raise ValueError("Empty marketplace source")

    # Local path first (absolute or starts with ./ or ~/)
    if s.startswith(("/", "./", "../", "~/")) or s == ".":
        p = Path(s).expanduser().resolve()
        if not p.exists():
            raise ValueError(f"Path does not exist: {p}")
        if p.is_dir():
            return MarketplaceSource(kind="directory", value=str(p))
        return MarketplaceSource(kind="file", value=str(p))

    # Git SSH
    if s.startswith(("git@", "ssh://")):
        return MarketplaceSource(kind="git", value=s)

    # URL
    if s.startswith(("http://", "https://")):
        if s.endswith(".json"):
            return MarketplaceSource(kind="url", value=s)
        return MarketplaceSource(kind="git", value=s)

    # GitHub shorthand
    if _GITHUB_SHORTHAND_RE.match(s):
        return MarketplaceSource(kind="github", value=s)

    raise ValueError(
        f"Unrecognized marketplace source: {s!r}. "
        "Try owner/repo, https://..., or /path/to/local"
    )


# ── Known-marketplaces config ────────────────────────────────────────

def load_known_marketplaces() -> dict[str, dict[str, Any]]:
    f = _known_marketplaces_file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def save_known_marketplaces(cfg: dict[str, dict[str, Any]]) -> None:
    _known_marketplaces_file().write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


# ── Installed-plugins registry ───────────────────────────────────────

def load_installed_plugins() -> dict[str, dict[str, Any]]:
    """Load installed plugins, handling both v2 (official) and flat (ccb-py) formats.

    v2 format::

        {"version": 2, "plugins": {"name@mkt": [{"installPath": "...", ...}]}}

    Flat format::

        {"name@mkt": {"path": "...", "enabled": true, ...}}
    """
    f = _installed_plugins_file()
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text())
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    # Detect v2 format
    if "version" in raw and "plugins" in raw:
        plugins_raw = raw["plugins"]
        if not isinstance(plugins_raw, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for pid, entries in plugins_raw.items():
            # v2 stores a list of install records; use the first one
            if isinstance(entries, list) and entries:
                entry = entries[0]
            elif isinstance(entries, dict):
                entry = entries
            else:
                continue
            mkt = pid.split("@", 1)[1] if "@" in pid else ""
            result[pid] = {
                "path": entry.get("installPath", ""),
                "enabled": entry.get("enabled", True),
                "description": entry.get("description", ""),
                "version": entry.get("version", ""),
                "marketplace": mkt,
                "_v2_entry": entry,  # preserve original for save
            }
        return result
    # Flat / ccb-py format
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def save_installed_plugins(cfg: dict[str, dict[str, Any]]) -> None:
    """Save installed plugins, preserving v2 format if the file already uses it."""
    f = _installed_plugins_file()
    # Check if existing file uses v2 format
    is_v2 = False
    try:
        if f.exists():
            existing = json.loads(f.read_text())
            is_v2 = isinstance(existing, dict) and "version" in existing and "plugins" in existing
    except Exception:
        pass

    if is_v2:
        # Write back in v2 format
        plugins_out: dict[str, Any] = {}
        for pid, info in cfg.items():
            v2_entry = info.pop("_v2_entry", None) if isinstance(info, dict) else None
            if v2_entry and isinstance(v2_entry, dict):
                # Update mutable fields in the original v2 entry
                if "enabled" in info:
                    v2_entry["enabled"] = info["enabled"]
                plugins_out[pid] = [v2_entry]
            else:
                # New plugin added by ccb-py — create a v2 record
                plugins_out[pid] = [{
                    "scope": "user",
                    "installPath": info.get("path", ""),
                    "version": info.get("version", ""),
                    "installedAt": info.get("installedAt", ""),
                    "lastUpdated": info.get("lastUpdated", ""),
                }]
        f.write_text(json.dumps({"version": 2, "plugins": plugins_out}, indent=2, ensure_ascii=False))
    else:
        # Strip internal keys before saving flat format
        clean = {}
        for pid, info in cfg.items():
            if isinstance(info, dict):
                clean[pid] = {k: v for k, v in info.items() if not k.startswith("_")}
            else:
                clean[pid] = info
        f.write_text(json.dumps(clean, indent=2, ensure_ascii=False))


# ── Identifier parsing ───────────────────────────────────────────────

def parse_plugin_identifier(ident: str) -> tuple[str, str | None]:
    """Parse "name" or "name@marketplace"."""
    if "@" in ident:
        parts = ident.split("@", 1)
        return parts[0], parts[1] or None
    return ident, None


# ── Git helpers ──────────────────────────────────────────────────────

def _run_git(*args: str, cwd: Path | None = None) -> tuple[bool, str]:
    """Run git and return (ok, combined output)."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=180,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out
    except FileNotFoundError:
        return False, "git not installed on this system"
    except subprocess.TimeoutExpired:
        return False, "git operation timed out after 180s"
    except Exception as e:
        return False, f"git error: {e}"


def _clone_github(repo: str, dest: Path, ref: str | None = None) -> tuple[bool, str]:
    """Clone ``owner/repo`` via HTTPS (fallback SSH).

    dest must not exist. We shallow-clone unless a ref is pinned.
    """
    if dest.exists():
        shutil.rmtree(dest)
    https_url = f"https://github.com/{repo}.git"
    args = ["clone", "--depth", "1"]
    if ref:
        args += ["--branch", ref]
    args += [https_url, str(dest)]
    ok, out = _run_git(*args)
    if ok:
        return True, out
    # Try SSH fallback
    shutil.rmtree(dest, ignore_errors=True)
    ssh_url = f"git@github.com:{repo}.git"
    args = ["clone", "--depth", "1"]
    if ref:
        args += ["--branch", ref]
    args += [ssh_url, str(dest)]
    ok2, out2 = _run_git(*args)
    if ok2:
        return True, out2
    return False, f"HTTPS clone failed: {out.strip()[-300:]}\nSSH fallback also failed: {out2.strip()[-300:]}"


def _clone_git(url: str, dest: Path, ref: str | None = None) -> tuple[bool, str]:
    if dest.exists():
        shutil.rmtree(dest)
    args = ["clone", "--depth", "1"]
    if ref:
        args += ["--branch", ref]
    args += [url, str(dest)]
    return _run_git(*args)


# ── Marketplace read/load ────────────────────────────────────────────

def _find_marketplace_json(root: Path) -> Path | None:
    """Locate marketplace.json in a cached marketplace directory.

    Checks: <root>/.claude-plugin/marketplace.json first (official layout),
    then <root>/marketplace.json (flat layout).
    """
    cands = [
        root / ".claude-plugin" / "marketplace.json",
        root / "marketplace.json",
    ]
    for c in cands:
        if c.exists():
            return c
    return None


def load_marketplace_manifest(name: str) -> dict[str, Any] | None:
    """Read a materialized marketplace's manifest from cache."""
    cfg = load_known_marketplaces()
    entry = cfg.get(name)
    if not entry:
        return None
    loc = entry.get("installLocation")
    if not loc:
        return None
    path = Path(loc)
    src_kind = entry.get("source", {}).get("source", "")
    if src_kind == "url":
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    if src_kind == "file":
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    # github, git, directory → a folder
    mp = _find_marketplace_json(path)
    if not mp:
        return None
    try:
        return json.loads(mp.read_text())
    except Exception:
        return None


# ── Marketplace materialization ──────────────────────────────────────

def materialize_marketplace(source: MarketplaceSource, suggested_name: str = "") -> tuple[str, str]:
    """Download / clone / read a marketplace source.

    Returns ``(name, installLocation)``. The ``name`` comes from the
    marketplace's own ``marketplace.json`` if present, else ``suggested_name``
    or a derived fallback.

    Raises ``RuntimeError`` on any failure.
    """
    cache = _marketplaces_cache_dir()

    # Temp name used for the download target — renamed to manifest.name later
    if source.kind == "github":
        tmp_name = source.value.replace("/", "__")
    elif source.kind == "git":
        tmp_name = re.sub(r"[^A-Za-z0-9_-]+", "_", source.value)[-40:] or "git-source"
    elif source.kind == "url":
        tmp_name = re.sub(r"[^A-Za-z0-9_-]+", "_", source.value)[-40:] or "url-source"
    else:
        tmp_name = Path(source.value).name or "local-source"

    if source.kind == "github":
        dest = cache / tmp_name
        ok, out = _clone_github(source.value, dest, source.ref)
        if not ok:
            raise RuntimeError(f"Failed to clone {source.value}: {out}")
        install_location = str(dest)

    elif source.kind == "git":
        dest = cache / tmp_name
        ok, out = _clone_git(source.value, dest, source.ref)
        if not ok:
            raise RuntimeError(f"Failed to clone {source.value}: {out}")
        install_location = str(dest)

    elif source.kind == "url":
        # Fetch JSON directly
        import urllib.request
        dest = cache / f"{tmp_name}.json"
        try:
            with urllib.request.urlopen(source.value, timeout=60) as resp:
                dest.write_bytes(resp.read())
        except Exception as e:
            raise RuntimeError(f"Failed to fetch {source.value}: {e}")
        install_location = str(dest)

    elif source.kind == "directory":
        # Point at the user's local directory — don't copy
        install_location = source.value

    elif source.kind == "file":
        install_location = source.value

    else:
        raise RuntimeError(f"Unsupported source kind: {source.kind}")

    # Resolve marketplace name from the manifest
    name = suggested_name or tmp_name
    if source.kind == "url":
        try:
            data = json.loads(Path(install_location).read_text())
            name = data.get("name") or name
        except Exception:
            pass
    elif source.kind == "file":
        try:
            data = json.loads(Path(install_location).read_text())
            name = data.get("name") or name
        except Exception:
            pass
    else:
        mp = _find_marketplace_json(Path(install_location))
        if mp:
            try:
                data = json.loads(mp.read_text())
                name = data.get("name") or name
            except Exception:
                pass

    # For cloned / URL sources, rename to marketplace name for clean lookups
    if source.kind in ("github", "git") and name != tmp_name:
        new_path = cache / name
        if new_path.exists():
            shutil.rmtree(new_path)
        try:
            Path(install_location).rename(new_path)
            install_location = str(new_path)
        except Exception:
            pass  # non-fatal; we keep the original path

    return name, install_location


# ── High-level marketplace ops ───────────────────────────────────────

def marketplace_add(source_str: str) -> str:
    """Add a marketplace by source string. Returns the marketplace name."""
    source = parse_marketplace_input(source_str)
    # Derive a suggested name from source for github shorthand (just the repo)
    suggested = ""
    if source.kind == "github":
        suggested = source.value.split("/", 1)[-1]
    name, loc = materialize_marketplace(source, suggested_name=suggested)
    cfg = load_known_marketplaces()
    cfg[name] = {
        "source": source.to_dict(),
        "installLocation": loc,
    }
    save_known_marketplaces(cfg)
    return name


def marketplace_remove(name: str) -> bool:
    cfg = load_known_marketplaces()
    if name not in cfg:
        return False
    loc = cfg[name].get("installLocation", "")
    src_kind = cfg[name].get("source", {}).get("source", "")
    # Only delete for cache-owned sources
    if src_kind in ("github", "git", "url"):
        try:
            p = Path(loc)
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.is_file():
                p.unlink(missing_ok=True)
        except Exception:
            pass
    del cfg[name]
    save_known_marketplaces(cfg)
    # Also uninstall any plugins that came from this marketplace
    installed = load_installed_plugins()
    changed = False
    for pid in list(installed.keys()):
        if pid.endswith(f"@{name}"):
            _remove_plugin_files(installed[pid].get("path", ""))
            del installed[pid]
            changed = True
    if changed:
        save_installed_plugins(installed)
    return True


def marketplace_update(name: str) -> bool:
    """Re-materialize an existing marketplace (git pull / re-fetch)."""
    cfg = load_known_marketplaces()
    entry = cfg.get(name)
    if not entry:
        return False
    src_kind = entry.get("source", {}).get("source", "")
    loc = entry.get("installLocation", "")
    if src_kind in ("github", "git") and Path(loc).is_dir():
        ok, _ = _run_git("pull", "--ff-only", cwd=Path(loc))
        return ok
    # For URL/file/directory: re-materialize
    try:
        source = MarketplaceSource.from_dict(entry["source"])
        new_name, new_loc = materialize_marketplace(source, suggested_name=name)
        cfg[new_name] = {"source": source.to_dict(), "installLocation": new_loc}
        save_known_marketplaces(cfg)
        return True
    except Exception:
        return False


def marketplace_list() -> list[dict[str, Any]]:
    """Return a list of marketplaces with their plugin counts."""
    cfg = load_known_marketplaces()
    out: list[dict[str, Any]] = []
    for name, entry in cfg.items():
        manifest = load_marketplace_manifest(name)
        plugins = manifest.get("plugins", []) if manifest else []
        out.append({
            "name": name,
            "source": entry.get("source", {}),
            "path": entry.get("installLocation", ""),
            "plugin_count": len(plugins),
            "plugins": [p.get("name", "") for p in plugins],
        })
    return out


def marketplace_browse(marketplace_name: str | None = None) -> list[dict[str, Any]]:
    """Return all installable plugins from marketplaces.

    Each entry: ``{name, description, marketplace, source, installed, enabled}``.
    If *marketplace_name* is given, only list plugins from that marketplace.
    """
    installed = load_installed_plugins()
    installed_ids = set(installed.keys())
    cfg = load_known_marketplaces()
    out: list[dict[str, Any]] = []
    targets = {marketplace_name: cfg[marketplace_name]} if marketplace_name and marketplace_name in cfg else cfg
    for mkt_name, _entry in targets.items():
        manifest = load_marketplace_manifest(mkt_name)
        if not manifest:
            continue
        for p in manifest.get("plugins", []):
            pname = p.get("name", "")
            pid = f"{pname}@{mkt_name}"
            is_installed = pid in installed_ids
            out.append({
                "name": pname,
                "description": p.get("description", ""),
                "marketplace": mkt_name,
                "plugin_id": pid,
                "source": p.get("source", {}),
                "installed": is_installed,
                "enabled": installed.get(pid, {}).get("enabled", True) if is_installed else False,
            })
    return out


# ── Plugin install/uninstall ─────────────────────────────────────────

def _resolve_plugin(name: str, marketplace: str | None) -> tuple[dict[str, Any], str] | None:
    """Find a plugin entry. Returns (entry, marketplace_name) or None."""
    cfg = load_known_marketplaces()
    if marketplace:
        if marketplace not in cfg:
            return None
        manifest = load_marketplace_manifest(marketplace)
        if not manifest:
            return None
        for p in manifest.get("plugins", []):
            if p.get("name") == name:
                return p, marketplace
        return None
    # Search all marketplaces
    for mkt_name in cfg:
        manifest = load_marketplace_manifest(mkt_name)
        if not manifest:
            continue
        for p in manifest.get("plugins", []):
            if p.get("name") == name:
                return p, mkt_name
    return None


def _materialize_plugin(entry: dict[str, Any], marketplace_name: str) -> str:
    """Download/copy a plugin's files into ~/.claude/plugins/<name>/.

    Returns the filesystem path where the plugin lives.
    """
    name = entry["name"]
    plugin_dir = _plugins_dir() / "installed" / name
    plugin_dir.parent.mkdir(parents=True, exist_ok=True)
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)

    src_cfg = entry.get("source") or {}
    src_kind = src_cfg.get("source", "")

    if src_kind == "github":
        ok, out = _clone_github(src_cfg["repo"], plugin_dir, src_cfg.get("ref"))
        if not ok:
            raise RuntimeError(f"Failed to clone plugin source: {out}")
    elif src_kind == "git":
        ok, out = _clone_git(src_cfg["url"], plugin_dir, src_cfg.get("ref"))
        if not ok:
            raise RuntimeError(f"Failed to clone plugin source: {out}")
    elif src_kind in ("directory", "file", ""):
        # Plugin lives inside the marketplace repo — copy the relative subdir
        # (if entry specifies one), else the whole marketplace dir.
        mkt_cfg = load_known_marketplaces().get(marketplace_name, {})
        mkt_root = Path(mkt_cfg.get("installLocation", ""))
        sub = src_cfg.get("path") or entry.get("path") or name
        sub_path = mkt_root / sub if not Path(sub).is_absolute() else Path(sub)
        if not sub_path.exists():
            # Fallback: look for <mkt_root>/plugins/<name> or <mkt_root>/<name>
            for cand in (mkt_root / "plugins" / name, mkt_root / name):
                if cand.exists():
                    sub_path = cand
                    break
        if not sub_path.exists():
            raise RuntimeError(f"Plugin source path not found: {sub_path}")
        shutil.copytree(sub_path, plugin_dir)
    else:
        raise RuntimeError(f"Unsupported plugin source: {src_kind}")

    return str(plugin_dir)


def plugin_install(identifier: str) -> dict[str, Any]:
    """Install ``name`` or ``name@marketplace``. Returns info dict."""
    name, marketplace = parse_plugin_identifier(identifier)
    # Auto-install the official marketplace if user typed an @official shorthand
    if marketplace == OFFICIAL_MARKETPLACE_NAME:
        cfg = load_known_marketplaces()
        if OFFICIAL_MARKETPLACE_NAME not in cfg:
            marketplace_add(OFFICIAL_MARKETPLACE_REPO)

    resolved = _resolve_plugin(name, marketplace)
    if not resolved:
        location = (
            f"marketplace '{marketplace}'" if marketplace
            else "any configured marketplace"
        )
        raise RuntimeError(f"Plugin '{name}' not found in {location}")
    entry, mkt_name = resolved

    path = _materialize_plugin(entry, mkt_name)
    plugin_id = f"{name}@{mkt_name}"
    installed = load_installed_plugins()
    installed[plugin_id] = {
        "path": path,
        "enabled": True,
        "description": entry.get("description", ""),
        "version": entry.get("version", ""),
        "marketplace": mkt_name,
    }
    save_installed_plugins(installed)
    return {
        "id": plugin_id,
        "name": name,
        "marketplace": mkt_name,
        "path": path,
        "description": entry.get("description", ""),
    }


def _remove_plugin_files(path: str) -> None:
    if not path:
        return
    try:
        p = Path(path)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


def plugin_uninstall(identifier: str) -> bool:
    installed = load_installed_plugins()
    name, marketplace = parse_plugin_identifier(identifier)

    # Find matching plugin id
    candidates: list[str] = []
    if marketplace:
        pid = f"{name}@{marketplace}"
        if pid in installed:
            candidates.append(pid)
    else:
        candidates = [pid for pid in installed if pid.split("@", 1)[0] == name]

    if not candidates:
        return False
    for pid in candidates:
        _remove_plugin_files(installed[pid].get("path", ""))
        del installed[pid]
    save_installed_plugins(installed)
    return True


def plugin_list() -> list[dict[str, Any]]:
    installed = load_installed_plugins()
    return [
        {"id": pid, **info}
        for pid, info in installed.items()
    ]


def plugin_set_enabled(identifier: str, enabled: bool) -> bool:
    installed = load_installed_plugins()
    name, marketplace = parse_plugin_identifier(identifier)
    pid = f"{name}@{marketplace}" if marketplace else None
    if pid and pid in installed:
        installed[pid]["enabled"] = enabled
        save_installed_plugins(installed)
        return True
    # Try matching by name only
    matches = [p for p in installed if p.split("@", 1)[0] == name]
    if not matches:
        return False
    for m in matches:
        installed[m]["enabled"] = enabled
    save_installed_plugins(installed)
    return True


# ── Discovery: slash commands contributed by plugins ─────────────────

def discover_plugin_slash_commands() -> dict[str, dict[str, str]]:
    """Return ``{slash_name: {path, description, plugin}}``.

    Scans every installed+enabled plugin for:

    1. ``commands/*.md``  — exposed as ``/<plugin>:<stem>``
    2. ``skills/<name>/SKILL.md`` — exposed as ``/<plugin>:<name>``

    This mirrors the official Claude Code plugin loader which discovers both
    ``commandsPath`` (``commands/``) and ``skillsPath`` (``skills/``).
    """
    out: dict[str, dict[str, str]] = {}
    for pid, info in load_installed_plugins().items():
        if not info.get("enabled", True):
            continue
        root = Path(info.get("path", ""))
        if not root.is_dir():
            continue
        # Plugin name for command prefix (e.g. "oh-my-claudecode")
        plugin_name = pid.split("@", 1)[0]

        # ── commands/*.md ──
        cmd_dir = root / "commands"
        if cmd_dir.is_dir():
            for f in cmd_dir.glob("*.md"):
                slash = f"/{plugin_name}:{f.stem}"
                desc = _extract_description(f)
                out[slash] = {
                    "path": str(f),
                    "description": desc,
                    "plugin": pid,
                }

        # ── skills/<name>/SKILL.md ──
        skills_dir = root / "skills"
        if skills_dir.is_dir():
            for skill_dir in skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    continue
                slash = f"/{plugin_name}:{skill_dir.name}"
                desc = _extract_description(skill_file)
                out[slash] = {
                    "path": str(skill_file),
                    "description": desc,
                    "plugin": pid,
                }
    return out


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Extract YAML frontmatter fields from a markdown file.

    Returns a dict with keys like ``name``, ``description``, ``argument-hint``.
    """
    try:
        text = path.read_text()
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def _extract_description(path: Path) -> str:
    """Get a description from a plugin markdown file.

    Tries (in order): frontmatter ``description``, first ``# Title``, filename.
    """
    fm = _parse_frontmatter(path)
    if fm.get("description"):
        return fm["description"][:100]
    # Fallback: first title or content line
    return _first_title(path) or path.stem


def _first_title(path: Path) -> str:
    try:
        in_frontmatter = False
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                continue
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()[:80]
            if stripped:
                return stripped[:80]
    except Exception:
        pass
    return ""
