"""System installer for ccb-py.

Handles PATH registration, shell completion generation,
and shell integration (bash/zsh/fish).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def get_install_info() -> dict[str, Any]:
    """Get information about the current installation."""
    exe = sys.executable
    pkg_dir = Path(__file__).parent
    bin_name = "ccb"
    # Check if ccb is in PATH
    try:
        result = subprocess.run(["which", bin_name], capture_output=True, text=True)
        in_path = result.returncode == 0
        bin_path = result.stdout.strip() if in_path else ""
    except FileNotFoundError:
        in_path = False
        bin_path = ""

    return {
        "python": exe,
        "package_dir": str(pkg_dir),
        "bin_name": bin_name,
        "in_path": in_path,
        "bin_path": bin_path,
        "shell": os.environ.get("SHELL", ""),
        "platform": sys.platform,
    }


# ---------------------------------------------------------------------------
# Shell completions
# ---------------------------------------------------------------------------

def generate_bash_completion() -> str:
    """Generate bash completion script for ccb."""
    from ccb.repl import SLASH_COMMAND_DESCRIPTIONS
    commands = " ".join(SLASH_COMMAND_DESCRIPTIONS.keys())
    return f'''# ccb bash completion
_ccb_completions() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}"
    local commands="{commands}"
    COMPREPLY=( $(compgen -W "$commands" -- "$cur") )
}}
complete -F _ccb_completions ccb
'''


def generate_zsh_completion() -> str:
    """Generate zsh completion script for ccb."""
    from ccb.repl import SLASH_COMMAND_DESCRIPTIONS
    items = []
    for cmd, desc in SLASH_COMMAND_DESCRIPTIONS.items():
        safe_desc = desc.replace("'", "'\\''")
        items.append(f"    '{cmd}:{safe_desc}'")
    commands_str = "\n".join(items)
    return f'''#compdef ccb
# ccb zsh completion

_ccb() {{
    local -a commands
    commands=(
{commands_str}
    )
    _describe 'command' commands
}}

_ccb "$@"
'''


def generate_fish_completion() -> str:
    """Generate fish completion script for ccb."""
    from ccb.repl import SLASH_COMMAND_DESCRIPTIONS
    lines = ["# ccb fish completion"]
    for cmd, desc in SLASH_COMMAND_DESCRIPTIONS.items():
        safe = desc.replace("'", "\\'")
        lines.append(f"complete -c ccb -a '{cmd}' -d '{safe}'")
    return "\n".join(lines)


def install_shell_completion(shell: str | None = None) -> tuple[bool, str]:
    """Install shell completion for the current shell."""
    shell = shell or os.path.basename(os.environ.get("SHELL", "bash"))

    if "zsh" in shell:
        comp = generate_zsh_completion()
        target = Path.home() / ".zsh" / "completions" / "_ccb"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(comp)
        return True, f"Installed zsh completion to {target}\nRun: autoload -Uz compinit && compinit"

    elif "fish" in shell:
        comp = generate_fish_completion()
        target = Path.home() / ".config" / "fish" / "completions" / "ccb.fish"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(comp)
        return True, f"Installed fish completion to {target}"

    elif "bash" in shell:
        comp = generate_bash_completion()
        target = Path.home() / ".bash_completion.d" / "ccb"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(comp)
        return True, f"Installed bash completion to {target}\nAdd to .bashrc: source {target}"

    return False, f"Unsupported shell: {shell}"


def add_to_path() -> tuple[bool, str]:
    """Add ccb to the user's PATH if not already there."""
    info = get_install_info()
    if info["in_path"]:
        return True, f"ccb already in PATH: {info['bin_path']}"

    # Find where pip installs scripts
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "-f", "ccb"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Location:"):
                    loc = line.split(":", 1)[1].strip()
                    # Scripts are typically in the bin dir parallel to site-packages
                    bin_dir = Path(loc).parent.parent / "bin"
                    if bin_dir.exists():
                        return True, f"Add to PATH: export PATH=\"{bin_dir}:$PATH\""
    except Exception:
        pass

    return False, "Could not determine install location. Try: pip install -e ."
