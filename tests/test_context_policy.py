from __future__ import annotations

from ccb.context_policy import get_context_policy, should_trigger_offload


def test_context_policy_defaults():
    policy = get_context_policy()

    assert policy.collapse_trigger_ratio == 0.72
    assert policy.mild_offload_ratio == 0.70
    assert policy.aggressive_offload_ratio == 0.85


def test_should_trigger_offload_respects_thresholds():
    assert should_trigger_offload(0.69) == (False, "")
    assert should_trigger_offload(0.70) == (True, "Mild offload: context above 70%")
    assert should_trigger_offload(0.85) == (True, "Aggressive compress: context above 85%")
