"""Safe argv construction and execution for install/update actions."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable


_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+:-]{0,254}$")


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    label: str


def _safe(ref: str) -> str:
    if not _REF.fullmatch(ref):
        raise ValueError(f"invalid application reference: {ref!r}")
    return ref


def action_plan(kind: str, action: str, ref: str) -> tuple[Command, ...]:
    ref = _safe(ref)
    if action not in {"install", "remove", "open"}:
        raise ValueError(f"unknown action: {action}")
    if kind == "repo":
        if action == "open":
            return (Command(("gtk-launch", ref), "Se deschide aplicația"),)
        return (Command(("pkexec", "/usr/lib/glue-hub/pkg.sh", action, ref),
                        "Se modifică aplicația"),)
    if kind == "flatpak":
        if action == "open":
            return (Command(("flatpak", "run", ref), "Se deschide aplicația"),)
        if action == "install":
            return (
                Command(("flatpak", "remote-add", "--user", "--if-not-exists",
                         "flathub", "https://flathub.org/repo/flathub.flatpakrepo"),
                        "Se pregătește Flathub"),
                Command(("flatpak", "install", "-y", "--user", "flathub", ref),
                        "Se instalează aplicația"),
            )
        return (Command(("flatpak", "uninstall", "-y", "--user", ref),
                        "Se dezinstalează aplicația"),)
    if kind == "aur":
        if action == "open":
            return (Command(("gtk-launch", ref), "Se deschide aplicația"),)
        if action == "remove":
            return (Command(("pkexec", "/usr/lib/glue-hub/pkg.sh", "remove", ref),
                            "Se dezinstalează aplicația"),)
        return (Command((
            "yay", "-S", "--aur", "--needed", "--noconfirm",
            "--answerclean", "None", "--answerdiff", "None",
            "--answeredit", "None", "--sudo", "pkexec", ref,
        ), "Se construiește și se instalează aplicația comunitară"),)
    raise ValueError(f"unknown source: {kind}")


def update_plan() -> tuple[Command, ...]:
    return (
        Command(("pkexec", "/usr/lib/glue-hub/update.sh"), "Se actualizează sistemul"),
        Command(("flatpak", "update", "-y", "--user"), "Se actualizează aplicațiile Flathub"),
        Command(("yay", "-Sua", "--noconfirm", "--answerclean", "None",
                 "--answerdiff", "None", "--answeredit", "None",
                 "--sudo", "pkexec"), "Se actualizează aplicațiile AUR"),
    )


def run_commands(
    commands: Iterable[Command],
    on_output: Callable[[str], None] | None = None,
) -> int:
    for command in commands:
        if on_output:
            on_output(f"{command.label}…\n")
        try:
            process = subprocess.Popen(
                command.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except OSError as exc:
            if on_output:
                on_output(f"Nu s-a putut porni: {exc}\n")
            return 127
        assert process.stdout is not None
        for line in process.stdout:
            if on_output:
                on_output(line)
        code = process.wait()
        if code:
            return code
    return 0
