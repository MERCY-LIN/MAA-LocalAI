"""Persistent campaign scheduling policy and state."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .evidence import (
    EvidenceError,
    read_json,
    utc_now,
    validate_identifier,
    write_json_atomic,
)


CAMPAIGN_SCHEMA = "localai.campaign/v1"


class CampaignError(ValueError):
    """Raised when campaign state or policy is invalid."""


@dataclass(frozen=True)
class CampaignPolicy:
    max_attempts_per_stage: int = 3
    max_total_attempts: int = 6
    max_elapsed_seconds: int = 3600
    max_sanity: int | None = None
    sanity_costs: dict[str, int] = field(default_factory=dict)

    def validate(self, stages: list[str]) -> None:
        if not stages:
            raise CampaignError("campaign requires at least one stage")
        if len(set(stages)) != len(stages):
            raise CampaignError("campaign stages must be unique")
        if self.max_attempts_per_stage < 1:
            raise CampaignError("max_attempts_per_stage must be positive")
        if self.max_total_attempts < 1:
            raise CampaignError("max_total_attempts must be positive")
        if self.max_elapsed_seconds < 1:
            raise CampaignError("max_elapsed_seconds must be positive")
        if self.max_sanity is not None and self.max_sanity < 0:
            raise CampaignError("max_sanity must be non-negative")
        for stage in stages:
            cost = self.sanity_costs.get(stage)
            if cost is None:
                raise CampaignError(
                    f"sanity cost is required before campaign progression: {stage}"
                )
            if cost < 0:
                raise CampaignError(f"sanity cost is negative: {stage}")
        if self.max_sanity is None and any(
            self.sanity_costs[stage] > 0 for stage in stages
        ):
            raise CampaignError(
                "max_sanity is required when a stage consumes sanity"
            )


@dataclass(frozen=True)
class CampaignAttempt:
    stage: str
    job: str
    run_id: str | None
    attempt_id: str | None
    status: str
    outcome: str | None
    sanity_cost: int
    formal_battle_started: bool
    finished_at: str


@dataclass
class CampaignState:
    campaign_id: str
    stages: list[str]
    policy: CampaignPolicy
    status: str = "ACTIVE"
    stage_index: int = 0
    next_candidate_index: dict[str, int] = field(default_factory=dict)
    attempts: list[CampaignAttempt] = field(default_factory=list)
    sanity_spent: int = 0
    started_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    pause_reason: str | None = None
    recovery_evidence: str | None = None

    @property
    def current_stage(self) -> str | None:
        if 0 <= self.stage_index < len(self.stages):
            return self.stages[self.stage_index]
        return None

    def attempts_for_stage(self, stage: str) -> int:
        return sum(attempt.stage == stage for attempt in self.attempts)

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        current = now or datetime.now(UTC)
        return max(0.0, (current - datetime.fromisoformat(self.started_at)).total_seconds())

    def pause(self, reason: str) -> None:
        self.status = "PAUSED"
        self.pause_reason = reason
        self.updated_at = utc_now()

    def resume(self, evidence_id: str) -> None:
        if self.status != "PAUSED":
            raise CampaignError("campaign is not paused")
        if not evidence_id.strip():
            raise CampaignError("recovery evidence is required")
        self.status = "ACTIVE"
        self.pause_reason = None
        self.recovery_evidence = evidence_id.strip()
        self.updated_at = utc_now()

    def record_attempt(self, attempt: CampaignAttempt) -> None:
        self.attempts.append(attempt)
        self.sanity_spent += attempt.sanity_cost
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CAMPAIGN_SCHEMA,
            "campaign_id": self.campaign_id,
            "stages": self.stages,
            "policy": asdict(self.policy),
            "status": self.status,
            "stage_index": self.stage_index,
            "next_candidate_index": self.next_candidate_index,
            "attempts": [asdict(attempt) for attempt in self.attempts],
            "sanity_spent": self.sanity_spent,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "pause_reason": self.pause_reason,
            "recovery_evidence": self.recovery_evidence,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CampaignState:
        if payload.get("schema_version") != CAMPAIGN_SCHEMA:
            raise CampaignError("unsupported campaign state schema")
        policy_payload = payload.get("policy")
        if not isinstance(policy_payload, dict):
            raise CampaignError("campaign state has no policy")
        attempts_payload = payload.get("attempts")
        if not isinstance(attempts_payload, list):
            raise CampaignError("campaign state attempts must be a list")
        return cls(
            campaign_id=str(payload.get("campaign_id", "")),
            stages=[str(item) for item in payload.get("stages", [])],
            policy=CampaignPolicy(**policy_payload),
            status=str(payload.get("status", "ACTIVE")),
            stage_index=int(payload.get("stage_index", 0)),
            next_candidate_index={
                str(key): int(value)
                for key, value in dict(
                    payload.get("next_candidate_index") or {}
                ).items()
            },
            attempts=[
                CampaignAttempt(**item)
                for item in attempts_payload
                if isinstance(item, dict)
            ],
            sanity_spent=int(payload.get("sanity_spent", 0)),
            started_at=str(payload.get("started_at") or utc_now()),
            updated_at=str(payload.get("updated_at") or utc_now()),
            pause_reason=payload.get("pause_reason"),
            recovery_evidence=payload.get("recovery_evidence"),
        )


class CampaignStore:
    """Atomically persist one campaign before and after every attempt."""

    def __init__(self, evidence_root: str | Path, campaign_id: str):
        self.evidence_root = Path(evidence_root).resolve()
        self.campaign_id = validate_identifier(campaign_id, "campaign_id")
        self.root = self.evidence_root / "_campaigns" / self.campaign_id
        self.state_path = self.root / "state.json"

    @staticmethod
    def new_id() -> str:
        return f"campaign-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"

    def create(
        self,
        *,
        stages: list[str],
        policy: CampaignPolicy,
    ) -> CampaignState:
        if self.state_path.is_file():
            raise CampaignError(f"campaign already exists: {self.campaign_id}")
        policy.validate(stages)
        state = CampaignState(
            campaign_id=self.campaign_id,
            stages=list(stages),
            policy=policy,
        )
        self.save(state)
        return state

    def load(self) -> CampaignState:
        payload = read_json(self.state_path)
        if payload is None:
            raise CampaignError(f"campaign state not found: {self.campaign_id}")
        state = CampaignState.from_dict(payload)
        if state.campaign_id != self.campaign_id:
            raise CampaignError("campaign id does not match state file")
        state.policy.validate(state.stages)
        return state

    def save(self, state: CampaignState) -> None:
        if state.campaign_id != self.campaign_id:
            raise CampaignError("campaign state id mismatch")
        state.updated_at = utc_now()
        write_json_atomic(self.state_path, state.to_dict())

    def record_attempt(self, state: CampaignState, attempt: CampaignAttempt) -> None:
        state.record_attempt(attempt)
        self.save(state)


def parse_sanity_costs(values: list[str] | None) -> dict[str, int]:
    costs: dict[str, int] = {}
    for value in values or []:
        stage, separator, raw_cost = value.partition("=")
        if not separator or not stage.strip() or not raw_cost.strip():
            raise CampaignError(f"invalid sanity cost: {value!r}")
        if stage in costs:
            raise CampaignError(f"duplicate sanity cost: {stage}")
        try:
            cost = int(raw_cost)
        except ValueError as exception:
            raise CampaignError(f"invalid sanity cost: {value!r}") from exception
        if cost < 0:
            raise CampaignError(f"sanity cost must be non-negative: {stage}")
        costs[stage] = cost
    return costs
