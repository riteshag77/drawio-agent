"""Org governance/house-style rules — deterministic evaluator, SQLite store,
admin CRUD routes, and the end-to-end IR -> response wiring.

Mirrors the existing conventions: evaluator tests build IR objects directly
(no LLM, no DB) and assert on ``evaluate_governance_rules``'s public output;
store tests follow ``test_template_store.py``'s temp-DB-context pattern;
admin route tests follow ``test_admin_routes.py``'s token/auth pattern; the
pipeline test mirrors ``test_ir_pipeline.py``'s
``test_design_tradeoffs_flow_from_ir_to_response``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.adapters.outbound import governance_store
from app.adapters.outbound.config import env_config
from app.core.services import xml_pipeline
from app.core.services.governance import evaluate_governance_rules, findings_to_warnings
from app.domain.entities.governance import GovernanceRule
from app.domain.ir.base import Flow, Leaf
from app.domain.ir.corridor import Corridor
from app.domain.ir.data_flow import DataFlowIR
from app.domain.ir.security import SecurityIR
from tests.test_ir_pipeline import _IrEnabled, _no_styles, _run
from tests.test_network_compiler import realistic_ir


def _rule(kind: str, **overrides) -> GovernanceRule:
    defaults = dict(id="r1", kind=kind, message=f"house rule: {kind}")
    defaults.update(overrides)
    return GovernanceRule(**defaults)


def _security_ir(**overrides) -> SecurityIR:
    defaults = dict(
        corridors=[
            Corridor(id="zone_public", label="Public", leaves=[
                Leaf(id="alb", shape_type="aws_application_load_balancer", label="ALB"),
            ]),
            Corridor(id="zone_private", label="Private", leaves=[
                Leaf(id="app", shape_type="aws_ecs", label="App"),
                Leaf(id="legacydb", shape_type="aws_rds", label="Legacy DB"),
            ]),
        ],
        anchored=[],
        flows=[
            Flow(source="alb", target="app", label="HTTPS", protection="tls"),
        ],
        purpose="Protects the application tier from public traffic.",
    )
    defaults.update(overrides)
    return SecurityIR(**defaults)


class GovernanceEvaluatorTests(unittest.TestCase):
    """One pair (pass/fail) per rule kind, run against hand-built IR objects."""

    def test_required_corridor_passes_when_present(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("required_corridor", corridor_id="zone_public")])
        self.assertTrue(findings[0].passed)

    def test_required_corridor_fails_when_absent(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("required_corridor", corridor_id="zone_detect")])
        self.assertFalse(findings[0].passed)
        self.assertIn("zone_detect", findings[0].detail)

    def test_forbidden_corridor_passes_when_absent(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("forbidden_corridor", corridor_id="zone_detect")])
        self.assertTrue(findings[0].passed)

    def test_forbidden_corridor_fails_when_present(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("forbidden_corridor", corridor_id="zone_public")])
        self.assertFalse(findings[0].passed)

    def test_required_leaf_shape_passes_when_matched(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("required_leaf_shape", shape_type="aws_rds")])
        self.assertTrue(findings[0].passed)

    def test_required_leaf_shape_fails_when_missing(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("required_leaf_shape", shape_type="aws_waf")])
        self.assertFalse(findings[0].passed)
        self.assertIn("aws_waf", findings[0].detail)

    def test_required_leaf_shape_works_on_network_ir(self) -> None:
        """Network nests leaves instead of using corridors — the _all_leaves
        dispatcher must still find them via iter_leaves()."""
        ir = realistic_ir()
        findings = evaluate_governance_rules(ir, [_rule("required_leaf_shape", shape_type="aws_rds")])
        self.assertTrue(findings[0].passed)

    def test_forbidden_shape_type_passes_when_absent(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("forbidden_shape_type", shape_type="aws_waf")])
        self.assertTrue(findings[0].passed)

    def test_forbidden_shape_type_fails_and_names_the_offender(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("forbidden_shape_type", shape_type="aws_rds")])
        self.assertFalse(findings[0].passed)
        self.assertIn("Legacy DB", findings[0].detail)

    def test_required_overview_field_passes_when_filled(self) -> None:
        ir = _security_ir(purpose="Protects the application tier.")
        findings = evaluate_governance_rules(ir, [_rule("required_overview_field", overview_field="purpose")])
        self.assertTrue(findings[0].passed)

    def test_required_overview_field_fails_when_empty(self) -> None:
        ir = _security_ir(purpose="")
        findings = evaluate_governance_rules(ir, [_rule("required_overview_field", overview_field="purpose")])
        self.assertFalse(findings[0].passed)
        self.assertIn("purpose", findings[0].detail)

    def test_min_narrative_bullets_fails_below_threshold(self) -> None:
        ir = _security_ir(business_notes=["one bullet"])
        findings = evaluate_governance_rules(
            ir, [_rule("min_narrative_bullets", overview_field="business_notes", min_count=2)]
        )
        self.assertFalse(findings[0].passed)
        self.assertIn("has 1 bullet", findings[0].detail)

    def test_min_narrative_bullets_passes_at_threshold(self) -> None:
        ir = _security_ir(business_notes=["one", "two"])
        findings = evaluate_governance_rules(
            ir, [_rule("min_narrative_bullets", overview_field="business_notes", min_count=2)]
        )
        self.assertTrue(findings[0].passed)

    def test_required_flow_protection_fails_on_unprotected_boundary_flow(self) -> None:
        ir = _security_ir(flows=[Flow(source="alb", target="app", label="HTTP", protection="none")])
        findings = evaluate_governance_rules(
            ir, [_rule("required_flow_protection", min_protection="tls", boundary_only=True)]
        )
        self.assertFalse(findings[0].passed)
        self.assertIn("alb->app", findings[0].detail)

    def test_required_flow_protection_passes_when_protected(self) -> None:
        ir = _security_ir(flows=[Flow(source="alb", target="app", label="HTTPS", protection="tls")])
        findings = evaluate_governance_rules(
            ir, [_rule("required_flow_protection", min_protection="tls", boundary_only=True)]
        )
        self.assertTrue(findings[0].passed)

    def test_required_flow_protection_mtls_rejects_tls_only(self) -> None:
        ir = _security_ir(flows=[Flow(source="alb", target="app", label="HTTPS", protection="tls")])
        findings = evaluate_governance_rules(
            ir, [_rule("required_flow_protection", min_protection="mtls", boundary_only=True)]
        )
        self.assertFalse(findings[0].passed)

    def test_required_flow_protection_boundary_only_is_a_documented_noop_off_security(self) -> None:
        """_BOUNDARY_ZONE_IDS is only defined on SecurityIR — a boundary_only
        rule scoped to Data-flow (or any other non-Security view) silently
        loses its boundary-crossing check and falls back to the
        classification-only check, with no error. Locked in here as
        documented, accepted behavior (see the matching comment in
        governance.py) rather than left as an untested assumption: an
        unclassified, unprotected flow between two Data-flow phases must
        still pass even though it clearly "crosses" phase_ingest/phase_store,
        because boundary_only has nothing to check against on this IR."""
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[
                    Leaf(id="api", shape_type="aws_api_gateway", label="API"),
                ]),
                Corridor(id="phase_store", label="Store", leaves=[
                    Leaf(id="db", shape_type="aws_rds", label="DB"),
                ]),
            ],
            anchored=[],
            flows=[Flow(source="api", target="db", label="SQL", protection="none", classification="none")],
        )
        findings = evaluate_governance_rules(
            ir, [_rule("required_flow_protection", min_protection="tls", boundary_only=True)]
        )
        self.assertTrue(findings[0].passed)

    def test_forbidden_label_pattern_fails_on_match(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("forbidden_label_pattern", label_pattern="legacy")])
        self.assertFalse(findings[0].passed)
        self.assertIn("Legacy DB", findings[0].detail)

    def test_forbidden_label_pattern_passes_when_no_match(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("forbidden_label_pattern", label_pattern="deprecated")])
        self.assertTrue(findings[0].passed)

    def test_forbidden_label_pattern_bad_regex_never_crashes(self) -> None:
        """A malformed admin-authored regex must degrade to a pass, not raise."""
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("forbidden_label_pattern", label_pattern="[unclosed")])
        self.assertTrue(findings[0].passed)

    def test_view_scoping_skips_rules_for_other_views(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [_rule("required_corridor", corridor_id="zone_public", view="network")])
        self.assertEqual(findings, [])

    def test_disabled_rule_is_skipped(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(
            ir, [_rule("required_corridor", corridor_id="zone_detect", enabled=False)]
        )
        self.assertEqual(findings, [])

    def test_platform_scoping_skips_rules_for_other_platforms(self) -> None:
        ir = _security_ir(platform="gcp")
        findings = evaluate_governance_rules(
            ir, [_rule("required_corridor", corridor_id="zone_public", platform="aws")]
        )
        self.assertEqual(findings, [])

    def test_findings_to_warnings_only_includes_failures(self) -> None:
        ir = _security_ir()
        findings = evaluate_governance_rules(ir, [
            _rule("required_corridor", corridor_id="zone_public", message="must have public zone"),
            _rule("required_corridor", id="r2", corridor_id="zone_detect", message="must have detect zone"),
        ])
        warnings = findings_to_warnings(findings)
        self.assertEqual(len(warnings), 1)
        self.assertIn("must have detect zone", warnings[0])


class _TempGovernance:
    """Point governance_store at a throwaway SQLite file."""

    def __init__(self) -> None:
        self._saved: dict[str, str | None] = {}
        self._dir: tempfile.TemporaryDirectory | None = None

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        env = {"DIAGRAM_GOVERNANCE_PATH": str(Path(self._dir.name) / "governance.sqlite3")}
        for key, value in env.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value
        env_config._config_instance = None
        governance_store.reset_ready_flag()
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        env_config._config_instance = None
        governance_store.reset_ready_flag()
        if self._dir:
            self._dir.cleanup()
        return False


class GovernanceStoreTests(unittest.TestCase):
    def test_create_and_list(self) -> None:
        with _TempGovernance():
            governance_store.create_rule(_rule("required_corridor", corridor_id="zone_public"))
            rules = governance_store.list_rules()
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].kind, "required_corridor")
            self.assertEqual(rules[0].corridor_id, "zone_public")
            self.assertTrue(rules[0].id)

    def test_empty_message_is_rejected(self) -> None:
        with _TempGovernance():
            rule = GovernanceRule(kind="required_corridor", message="   ", corridor_id="zone_public")
            self.assertIsNone(governance_store.create_rule(rule))
            self.assertEqual(governance_store.list_rules(), [])

    def test_list_scoped_by_view_includes_any(self) -> None:
        with _TempGovernance():
            governance_store.create_rule(_rule("required_corridor", corridor_id="zone_public", view="security"))
            governance_store.create_rule(_rule("required_corridor", corridor_id="vpc", view="network"))
            governance_store.create_rule(_rule("required_corridor", corridor_id="x", view="any"))
            security_rules = governance_store.list_rules("security")
            self.assertEqual({r.view for r in security_rules}, {"security", "any"})

    def test_enabled_only_excludes_disabled_rules(self) -> None:
        with _TempGovernance():
            r = governance_store.create_rule(_rule("required_corridor", corridor_id="zone_public"))
            governance_store.update_rule(r.id, enabled=False)
            self.assertEqual(governance_store.list_rules(enabled_only=True), [])
            self.assertEqual(len(governance_store.list_rules()), 1)

    def test_update_changes_fields_and_preserves_id(self) -> None:
        with _TempGovernance():
            r = governance_store.create_rule(_rule("required_corridor", corridor_id="zone_public"))
            updated = governance_store.update_rule(r.id, message="new message", enabled=False)
            self.assertEqual(updated.id, r.id)
            self.assertEqual(updated.message, "new message")
            self.assertFalse(updated.enabled)

    def test_update_unknown_id_returns_none(self) -> None:
        with _TempGovernance():
            self.assertIsNone(governance_store.update_rule("nope", message="x"))

    def test_delete_removes_rule(self) -> None:
        with _TempGovernance():
            r = governance_store.create_rule(_rule("required_corridor", corridor_id="zone_public"))
            self.assertTrue(governance_store.delete_rule(r.id))
            self.assertEqual(governance_store.list_rules(), [])
            self.assertFalse(governance_store.delete_rule(r.id))


class _TempAdminPassword:
    def __init__(self, password: str | None) -> None:
        self.password = password

    def __enter__(self):
        self._saved = os.environ.get("ADMIN_PASSWORD")
        if self.password is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = self.password
        env_config._config_instance = None
        return self

    def __exit__(self, *exc):
        if self._saved is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = self._saved
        env_config._config_instance = None
        return False


class GovernanceAdminRoutesTests(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def _token(self, password: str) -> str:
        import hashlib
        import hmac

        return hmac.new(password.encode("utf-8"), b"admin-session-v1", hashlib.sha256).hexdigest()

    def test_crud_round_trip(self) -> None:
        with _TempGovernance(), _TempAdminPassword("secret123"):
            client = self._client()
            token = self._token("secret123")
            headers = {"X-Admin-Token": token}

            create_res = client.post(
                "/admin/governance-rules",
                json={"kind": "required_corridor", "message": "must have public zone", "corridor_id": "zone_public"},
                headers=headers,
            )
            self.assertEqual(create_res.status_code, 200)
            rule_id = create_res.json()["id"]
            self.assertTrue(rule_id)

            list_res = client.get("/admin/governance-rules", headers=headers)
            self.assertEqual(list_res.status_code, 200)
            self.assertEqual(len(list_res.json()["rules"]), 1)

            patch_res = client.patch(
                f"/admin/governance-rules/{rule_id}", json={"enabled": False}, headers=headers
            )
            self.assertEqual(patch_res.status_code, 200)
            self.assertFalse(patch_res.json()["enabled"])

            delete_res = client.delete(f"/admin/governance-rules/{rule_id}", headers=headers)
            self.assertEqual(delete_res.status_code, 200)
            self.assertTrue(delete_res.json()["deleted"])
            self.assertEqual(client.get("/admin/governance-rules", headers=headers).json()["rules"], [])

    def test_create_without_message_is_rejected(self) -> None:
        with _TempGovernance(), _TempAdminPassword("secret123"):
            client = self._client()
            headers = {"X-Admin-Token": self._token("secret123")}
            res = client.post(
                "/admin/governance-rules",
                json={"kind": "required_corridor", "message": "", "corridor_id": "zone_public"},
                headers=headers,
            )
            self.assertEqual(res.status_code, 400)

    def test_patch_unknown_rule_is_404(self) -> None:
        with _TempGovernance(), _TempAdminPassword("secret123"):
            client = self._client()
            headers = {"X-Admin-Token": self._token("secret123")}
            res = client.patch("/admin/governance-rules/nope", json={"enabled": False}, headers=headers)
            self.assertEqual(res.status_code, 404)

    def test_rules_route_rejects_without_token(self) -> None:
        with _TempGovernance(), _TempAdminPassword("secret123"):
            client = self._client()
            res = client.get("/admin/governance-rules")
            self.assertEqual(res.status_code, 401)


class GovernancePipelineTests(unittest.TestCase):
    """IR -> XmlDiagram.governance_findings -> _build_response, mirroring
    test_ir_pipeline.py's design_tradeoffs coverage.

    governance now defaults to False (matching cost_estimate/well_architected/
    compliance's opt-in shape — see docs on generate_xml_diagram_from_ir's
    include_governance_report), so every case here passes governance=True
    explicitly. Without it these would pass for the wrong reason: an empty
    findings list because the report was never requested, not because of
    what's actually under test (no rules configured, or a rule failing)."""

    def test_governance_findings_flow_from_ir_to_response(self) -> None:
        ir = realistic_ir()
        rule = _rule("required_leaf_shape", shape_type="aws_shield", message="must include AWS Shield")
        with _IrEnabled(), \
                patch("app.core.services.agent.generate_network_ir", return_value=ir), \
                patch("app.core.services.agent.list_governance_rules", return_value=[rule]), \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(governance=True)

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["governance_findings"]), 1)
        finding = result["governance_findings"][0]
        self.assertFalse(finding["passed"])
        self.assertEqual(finding["message"], "must include AWS Shield")

    def test_no_rules_returns_empty_governance_findings(self) -> None:
        ir = realistic_ir()
        with _IrEnabled(), \
                patch("app.core.services.agent.generate_network_ir", return_value=ir), \
                patch("app.core.services.agent.list_governance_rules", return_value=[]), \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(governance=True)

        self.assertEqual(result["governance_findings"], [])

    def test_governance_off_skips_evaluation_even_with_failing_rules(self) -> None:
        """The new default: a rule that would fail is never evaluated at all
        when the caller didn't ask for the report — not just hidden after the
        fact. Proven by patching evaluate_governance_rules itself and
        asserting it is never called, not merely by checking the output is
        empty (which an evaluated-but-passing result would also produce)."""
        ir = realistic_ir()
        rule = _rule("required_leaf_shape", shape_type="aws_shield", message="must include AWS Shield")
        with _IrEnabled(), \
                patch("app.core.services.agent.generate_network_ir", return_value=ir), \
                patch("app.core.services.agent.list_governance_rules", return_value=[rule]), \
                patch("app.core.services.agent.evaluate_governance_rules") as mock_eval, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run()  # governance not passed -> defaults to False

        self.assertEqual(result["governance_findings"], [])
        mock_eval.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class RelationalRuleTests(unittest.TestCase):
    """Every other rule kind asks whether something *exists*. Most real house
    rules are about where a thing sits and what may reach it — those were
    unexpressible, so they were enforced by review or not at all."""

    def _findings(self, rule: GovernanceRule, ir) -> list:
        return evaluate_governance_rules(ir, [rule])

    def test_a_leaf_in_the_wrong_zone_is_reported_by_name(self) -> None:
        rule = _rule("leaf_shape_in_corridor", shape_type="aws_rds",
                     corridor_id="zone_restricted")
        findings = self._findings(rule, _security_ir())
        self.assertEqual(len(findings), 1)
        self.assertFalse(findings[0].passed)
        self.assertIn("Legacy DB", findings[0].detail)

    def test_a_leaf_in_the_right_zone_passes(self) -> None:
        rule = _rule("leaf_shape_in_corridor", shape_type="aws_rds",
                     corridor_id="zone_private")
        self.assertTrue(self._findings(rule, _security_ir())[0].passed)

    def test_the_rule_says_where_not_whether(self) -> None:
        """Vacuous when the shape is absent — `required_leaf_shape` is the kind
        for "must exist", and conflating them would make one rule fail for two
        unrelated reasons."""
        rule = _rule("leaf_shape_in_corridor", shape_type="aws_dynamodb",
                     corridor_id="zone_restricted")
        self.assertTrue(self._findings(rule, _security_ir())[0].passed)

    def test_a_forbidden_zone_to_zone_flow_is_caught(self) -> None:
        ir = _security_ir(flows=[
            Flow(source="alb", target="legacydb", label="SQL", protection="tls"),
        ])
        rule = _rule("forbidden_corridor_flow", corridor_id="zone_public",
                     target_corridor_id="zone_private")
        findings = self._findings(rule, ir)
        self.assertFalse(findings[0].passed)
        self.assertIn("alb -> legacydb", findings[0].detail)

    def test_an_indirect_path_is_not_a_violation(self) -> None:
        """Direct flows only: a path *through* an intermediate zone is precisely
        what such a rule exists to require, so reporting it would invert the
        policy."""
        rule = _rule("forbidden_corridor_flow", corridor_id="zone_public",
                     target_corridor_id="zone_private")
        # alb -> app is public -> private, so use a rule whose endpoints are
        # not directly connected in the fixture.
        ir = _security_ir(flows=[Flow(source="alb", target="app", label="HTTPS", protection="tls")])
        offending = _rule("forbidden_corridor_flow", corridor_id="zone_private",
                          target_corridor_id="zone_public")
        self.assertTrue(self._findings(offending, ir)[0].passed)
        self.assertFalse(self._findings(rule, ir)[0].passed)

    def test_relational_rules_are_vacuous_on_a_view_without_zones(self) -> None:
        """Network has no corridor vocabulary; a rule scoped 'any' must not
        fail there for a grouping the view does not have."""
        rule = _rule("forbidden_corridor_flow", corridor_id="zone_public",
                     target_corridor_id="zone_private")
        findings = evaluate_governance_rules(realistic_ir(), [rule])
        self.assertTrue(all(f.passed for f in findings))
