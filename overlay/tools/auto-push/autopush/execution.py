"""Validated execution settings shared by single-stage and campaign runs."""

from __future__ import annotations

import math
from argparse import Namespace
from dataclasses import dataclass
from typing import Any


DEFAULT_VERIFY_THRESHOLD = 0.86


@dataclass(frozen=True)
class StageExecutionConfig:
    """All settings that can affect one formal stage execution."""

    stage: str
    copilot: str
    package_dir: str
    evidence_root: str
    dll: str | None = None
    resource_dir: str | None = None
    user_dir: str | None = None
    address: str = "emulator-5554"
    adb: str | None = None
    connect_config: str = "General"
    timeout: int = 600
    template_root: str | None = None
    state_memory_dir: str | None = None
    verify_threshold: float = DEFAULT_VERIFY_THRESHOLD
    formation: bool = False
    formation_index: int = 0
    add_trust: bool = False
    ignore_requirements: bool = False
    operbox_data: str | None = None
    support_unit_usage: int = 0
    is_raid: bool = False
    confirm_formal_battle: bool = False

    @classmethod
    def from_arguments(cls, arguments: Namespace) -> StageExecutionConfig:
        return cls(
            stage=str(getattr(arguments, "stage", "")),
            copilot=str(getattr(arguments, "copilot", "")),
            package_dir=str(getattr(arguments, "package_dir", "")),
            evidence_root=str(getattr(arguments, "evidence_root", "")),
            dll=getattr(arguments, "dll", None),
            resource_dir=getattr(arguments, "resource_dir", None),
            user_dir=getattr(arguments, "user_dir", None),
            address=str(getattr(arguments, "address", "emulator-5554")),
            adb=getattr(arguments, "adb", None),
            connect_config=str(getattr(arguments, "connect_config", "General")),
            timeout=int(getattr(arguments, "timeout", 600)),
            template_root=getattr(arguments, "template_root", None),
            state_memory_dir=getattr(arguments, "state_memory_dir", None),
            verify_threshold=float(
                getattr(arguments, "verify_threshold", DEFAULT_VERIFY_THRESHOLD)
            ),
            formation=bool(getattr(arguments, "formation", False)),
            formation_index=int(getattr(arguments, "formation_index", 0)),
            add_trust=bool(getattr(arguments, "add_trust", False)),
            ignore_requirements=bool(
                getattr(arguments, "ignore_requirements", False)
            ),
            operbox_data=getattr(arguments, "operbox_data", None),
            support_unit_usage=int(
                getattr(arguments, "support_unit_usage", 0)
            ),
            is_raid=bool(getattr(arguments, "is_raid", False)),
            confirm_formal_battle=bool(
                getattr(arguments, "confirm_formal_battle", False)
            ),
        )

    def validate(self) -> None:
        """Reject an incomplete or unsafe execution configuration."""

        if not self.stage.strip():
            raise ValueError("stage must not be empty")
        if not self.copilot.strip():
            raise ValueError("copilot must not be empty")
        if not math.isfinite(self.verify_threshold) or not (
            0.0 < self.verify_threshold <= 1.0
        ):
            raise ValueError("verify_threshold must be in (0, 1]")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.formation_index < 0:
            raise ValueError("formation_index must be non-negative")
        if self.support_unit_usage not in {0, 1, 2}:
            raise ValueError("support_unit_usage must be 0, 1, or 2")
    def to_namespace(self) -> Namespace:
        """Return the legacy namespace consumed by the debug runner."""

        return Namespace(**self.__dict__)


def config_value(config: StageExecutionConfig, name: str) -> Any:
    """Read one validated field, primarily for diagnostics and tests."""

    if name not in config.__dataclass_fields__:
        raise AttributeError(name)
    return getattr(config, name)
