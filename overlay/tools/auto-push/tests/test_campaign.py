from pathlib import Path

import pytest

from autopush.campaign import (
    CampaignAttempt,
    CampaignError,
    CampaignPolicy,
    CampaignStore,
    parse_sanity_costs,
)


def test_campaign_policy_requires_trackable_sanity_costs():
    policy = CampaignPolicy()

    with pytest.raises(CampaignError, match="sanity cost"):
        policy.validate(["OF-1"])


def test_campaign_policy_requires_a_total_sanity_limit():
    policy = CampaignPolicy(sanity_costs={"OF-1": 21})

    with pytest.raises(CampaignError, match="max_sanity"):
        policy.validate(["OF-1"])


def test_campaign_state_persists_attempts_and_recovery_gate(tmp_path: Path):
    policy = CampaignPolicy(
        sanity_costs={"OF-1": 21},
        max_sanity=42,
    )
    store = CampaignStore(tmp_path, "campaign-state")
    state = store.create(stages=["OF-1"], policy=policy)
    store.record_attempt(
        state,
        CampaignAttempt(
            stage="OF-1",
            job="job.json",
            run_id="run-1",
            attempt_id="attempt-1",
            status="FAILED",
            outcome="FAILED",
            sanity_cost=21,
            formal_battle_started=True,
            finished_at="2026-11-17T00:10:00+00:00",
        ),
    )
    state.pause("unknown result")
    store.save(state)

    loaded = store.load()

    assert loaded.sanity_spent == 21
    assert loaded.status == "PAUSED"
    assert loaded.attempts[0].job == "job.json"
    with pytest.raises(CampaignError, match="recovery evidence"):
        loaded.resume("")
    loaded.resume("recovery-snapshot.json")
    assert loaded.status == "ACTIVE"
    assert loaded.recovery_evidence == "recovery-snapshot.json"


def test_parse_sanity_costs_is_strict():
    assert parse_sanity_costs(["OF-1=21", "OF-2=18"]) == {
        "OF-1": 21,
        "OF-2": 18,
    }
    with pytest.raises(CampaignError, match="invalid sanity cost"):
        parse_sanity_costs(["OF-1"])
