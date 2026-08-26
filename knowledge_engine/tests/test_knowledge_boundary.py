from knowledge_engine.knowledge.rule_repository import RuleRepository
from knowledge_engine.knowledge.scoring_gate import get_scoring_gate_status
from knowledge_engine.knowledge.source_repository import SourceRepository
from knowledge_engine.knowledge.trust_policy import classify_rule


def test_rule_repository_is_empty_without_explicit_external_data(tmp_path):
    repository = RuleRepository(approved_dir=tmp_path / "approved")
    assert repository.list_rules() == []
    assert repository.search("kitchen southeast") == []


def test_source_repository_is_read_only_and_missing_safe(tmp_path):
    repository = SourceRepository(tmp_path / "source_packets")
    assert repository.list_packets() == []
    assert repository.get_packet("packet_001") is None


def test_tool_provenance_is_not_trusted():
    decision = classify_rule(
        {
            "status": "APPROVED",
            "expert_verified": True,
            "verified_by": "OpenAI Codex — recorded user-confirmed",
            "source_refs": {"source_id": "SRC_001"},
            "approved_by": "reviewer",
            "approved_at": "2026-01-01T00:00:00Z",
        }
    )
    assert decision.trusted is False
    assert any("tool-only provenance" in reason for reason in decision.reasons)


def test_scoring_gate_is_always_fail_closed():
    status = get_scoring_gate_status(
        [
            {
                "status": "APPROVED",
                "expert_verified": True,
                "verified_by": "Named Human Expert",
                "source_refs": {"source_id": "SRC_001"},
                "approved_by": "Named Reviewer",
                "approved_at": "2026-01-01T00:00:00Z",
            }
        ]
    )
    assert status["sqlite_rules_count"] == 1
    assert status["trusted_official_rules_count"] == 1
    assert status["official_scoring_enabled"] is False
    assert status["vastu_score"] is None
