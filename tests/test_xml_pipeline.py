"""Tests for run_xml_pipeline: deterministic XML repair (surgical edit path).

Fresh generation is IR-only. Malformed-XML retry lives on the 3D path only.
"""

from __future__ import annotations

import asyncio
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch

from app.adapters.outbound.diagram.xml_diagnostics import XmlDiagnostics
from app.adapters.outbound.diagram.xml_tools import extract_page_by_name
from app.core.logging import StepLogger
from app.core.services import xml_pipeline
from app.domain.entities import XmlDiagram

MINIMAL_XML = (
    '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
    "<mxGraphModel><root>"
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="iam_icon" value="IAM" style="html=1;" vertex="1" parent="1">'
    '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
    "</root></mxGraphModel></diagram></mxfile>"
)

# Substantial current_xml forces edit mode → surgical/direct XML (not IR).
_EDIT_BASE = "x" * 500


class DeterministicRepairOnlyTests(unittest.TestCase):
    """Format/structure repair must never call an LLM corrective pass."""

    def _run(self, *, diagnose_result, repair_side_effect):
        xml_diagram = XmlDiagram(xml=MINIMAL_XML, assistant_message="ok", suggestions=[], step_markers=[])
        with patch.object(xml_pipeline, "generate_xml_diagram", return_value=xml_diagram), \
                patch.object(xml_pipeline, "diagnose_xml", return_value=diagnose_result), \
                patch.object(xml_pipeline, "repair_xml", side_effect=repair_side_effect):
            return asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="rename IAM to Identity",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
                current_xml=_EDIT_BASE,
            ))

    def test_warnings_only_still_succeeds(self) -> None:
        warn_only = XmlDiagnostics(errors=[], warnings=["icon pitch 120px between 'a' and 'b' is outside range"])
        result = self._run(
            diagnose_result=warn_only,
            repair_side_effect=[(MINIMAL_XML, warn_only)],
        )
        self.assertEqual(result["status"], "success")

    def test_errors_do_not_trigger_llm_repair(self) -> None:
        """Residual structural errors are logged only — never sent to an LLM repair pass."""
        has_errors = XmlDiagnostics(errors=["dangling edge 'e1'"], warnings=[])
        with patch.object(xml_pipeline, "generate_xml_diagram", return_value=XmlDiagram(
            xml=MINIMAL_XML, assistant_message="ok", suggestions=[], step_markers=[],
        )), patch.object(xml_pipeline, "diagnose_xml", return_value=has_errors), \
                patch.object(xml_pipeline, "repair_xml", return_value=(MINIMAL_XML, has_errors)), \
                patch("app.core.services.agent.repair_xml_diagram", create=True) as llm_repair:
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="rename IAM to Identity",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
                current_xml=_EDIT_BASE,
            ))
        llm_repair.assert_not_called()
        self.assertEqual(result["status"], "success")
        self.assertNotIn("repair_xml_diagram", dir(xml_pipeline))


class MalformedXmlRetryTests(unittest.TestCase):
    """Generation retry on truncated XML is allowed on the 3D path."""

    TRUNCATED_XML = (
        '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
        "<mxGraphModel><root>"
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="broken" value="x" style="html=1;" vertex="1" parent="1">'
        '<mxGeometry x="0'
    )

    def _run(self, *, generate_side_effect):
        clean = XmlDiagnostics(errors=[], warnings=[])
        gen_mock = MagicMock(side_effect=generate_side_effect)
        with patch.object(xml_pipeline, "generate_xml_diagram", gen_mock), \
                patch.object(xml_pipeline, "diagnose_xml", return_value=clean), \
                patch.object(xml_pipeline, "repair_xml", return_value=(MINIMAL_XML, clean)):
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="isometric 3d aws architecture",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
                three_d=True,
            ))
        return gen_mock, result

    def test_malformed_first_attempt_retries_and_succeeds(self) -> None:
        truncated = XmlDiagram(xml=self.TRUNCATED_XML, assistant_message="ok", suggestions=[], step_markers=[])
        good = XmlDiagram(xml=MINIMAL_XML, assistant_message="ok", suggestions=[], step_markers=[])
        gen_mock, result = self._run(generate_side_effect=[truncated, good])

        self.assertEqual(gen_mock.call_count, 2)
        _, kwargs = gen_mock.call_args
        self.assertIn("retry_hint", kwargs)
        self.assertIsNotNone(kwargs["retry_hint"])
        self.assertEqual(result["status"], "success")
        self.assertIn("iam_icon", result["xml"])

    def test_malformed_on_retry_too_raises(self) -> None:
        truncated = XmlDiagram(xml=self.TRUNCATED_XML, assistant_message="ok", suggestions=[], step_markers=[])
        empty = XmlDiagram(xml="", assistant_message="ok", suggestions=[], step_markers=[])
        with self.assertRaises(ValueError):
            self._run(generate_side_effect=[truncated, empty])


class DegradedFeaturesTests(unittest.TestCase):
    def test_collects_quality_gate_and_quota(self) -> None:
        logger = StepLogger()
        logger.log(
            "views",
            "Logical view scored 40",
            level="warning",
            details={"degraded": True, "feature": "Logical view quality", "reason": "quality_gate"},
        )
        logger.log(
            "docs",
            "ADR notes branch failed: quota",
            level="warning",
            details={"quota_exceeded": True, "feature": "ADR notes"},
        )
        feats = xml_pipeline._collect_degraded_features(logger)
        by_feature = {f["feature"]: f["reason"] for f in feats}
        self.assertEqual(by_feature["Logical view quality"], "quality_gate")
        self.assertEqual(by_feature["ADR notes"], "quota")


def _page(name: str, cells: str) -> str:
    return (
        f'<diagram name="{name}" id="{name.lower()}">'
        "<mxGraphModel><root>"
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram>"
    )


def _seed_successful_surgical_edit(logger: StepLogger) -> None:
    """_was_successful_surgical_edit() reads this back off the logger — the
    real surgical path logs it from inside generate_xml_diagram, which the
    tests below mock out, so it must be seeded directly."""
    logger.log("surgical_edit", "Applied 2 surgical ops", level="success")


class SurgicalEditDocPageCarryForwardTests(unittest.TestCase):
    """Regression: a live capture showed ADR Notes / RAID pages vanish from
    the response after ANY surgical edit, even with adr_notes/raid_register
    still on in the request. Root cause: _generate_primary_page scopes
    current_xml down to just the primary page before the edit (it has no
    multi-page concept), and doc regeneration is deliberately skipped on a
    surgical edit (cost vs. value on a rename/move) — so the pages that
    existed in the pre-edit current_xml were never regenerated *and* never
    carried through untouched. Fixed by extracting them straight from the
    original current_xml and re-attaching them after the edit."""

    _CURRENT_XML = (
        '<mxfile host="app.diagrams.net">'
        + _page(
            "Network",
            '<mxCell id="ec2" value="EC2" style="html=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>',
        )
        + _page("ADR Notes", '<mxCell id="adr_table" value="&lt;table&gt;ADR-001&lt;/table&gt;" vertex="1" parent="1"/>')
        + _page("RAID", '<mxCell id="raid_table" value="&lt;table&gt;R-01&lt;/table&gt;" vertex="1" parent="1"/>')
        + "</mxfile>"
    )

    def _run(self, *, adr_notes: bool, raid_register: bool):
        xml_diagram = XmlDiagram(
            xml=_page("Network", '<mxCell id="ec2" value="Checkout Fn" style="html=1;" vertex="1" parent="1">'
                                  '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'),
            assistant_message="ok", suggestions=[], step_markers=[],
        )
        logger = StepLogger()
        _seed_successful_surgical_edit(logger)
        clean = XmlDiagnostics(errors=[], warnings=[])
        with patch.object(xml_pipeline, "generate_xml_diagram", return_value=xml_diagram), \
                patch.object(xml_pipeline, "diagnose_xml", return_value=clean), \
                patch.object(xml_pipeline, "repair_xml", return_value=(xml_diagram.xml, clean)), \
                patch.object(xml_pipeline, "_generate_adr_list") as adr_mock, \
                patch.object(xml_pipeline, "_generate_raid_list") as raid_mock:
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="rename EC2 to Checkout Fn",
                provider="gemini",
                platform="aws",
                logger=logger,
                diagram_renderer="local",
                current_xml=self._CURRENT_XML,
                adr_notes=adr_notes,
                raid_register=raid_register,
            ))
        adr_mock.assert_not_called()
        raid_mock.assert_not_called()
        return result

    def test_existing_adr_and_raid_pages_survive_a_surgical_edit(self) -> None:
        result = self._run(adr_notes=True, raid_register=True)
        self.assertEqual(result["status"], "success")
        self.assertIsNotNone(extract_page_by_name(result["xml"], "ADR Notes"))
        self.assertIsNotNone(extract_page_by_name(result["xml"], "RAID"))
        self.assertIn("Checkout Fn", result["xml"])  # the edit itself still applied

    def test_no_op_when_neither_doc_page_was_requested(self) -> None:
        result = self._run(adr_notes=False, raid_register=False)
        self.assertIsNone(extract_page_by_name(result["xml"], "ADR Notes"))
        self.assertIsNone(extract_page_by_name(result["xml"], "RAID"))


class SurgicalEditExtraViewCarryForwardTests(unittest.TestCase):
    """Regression: every extra tab (Logical/Security/Data-flow) regenerated
    via a full LLM call — with a corrective retry on a low quality score —
    on EVERY surgical edit, even a one-cell rename, because the fan-out
    condition never checked whether the edit was surgical. Measured live:
    the primary-only edit itself took ~3-10s; each regenerated extra view
    could add as much again. Fixed by carrying the pre-edit page through
    unchanged (same call already made for ADR/RAID) and surfacing which tabs
    are now possibly stale via the existing degraded_features banner."""

    _CURRENT_XML = (
        '<mxfile host="app.diagrams.net">'
        + _page(
            "Logical",
            '<mxCell id="svc" value="Orders" style="html=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>',
        )
        + _page(
            "Network",
            '<mxCell id="ec2" value="EC2" style="html=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>',
        )
        + "</mxfile>"
    )

    def test_extra_view_is_carried_forward_not_regenerated(self) -> None:
        xml_diagram = XmlDiagram(
            xml=_page("Network", '<mxCell id="ec2" value="Checkout Fn" style="html=1;" vertex="1" parent="1">'
                                  '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'),
            assistant_message="ok", suggestions=[], step_markers=[],
        )
        logger = StepLogger()
        _seed_successful_surgical_edit(logger)
        clean = XmlDiagnostics(errors=[], warnings=[])
        # repair_xml runs once per merged page (Logical, then Network) — it
        # must echo back whichever page it was actually given, not a single
        # fixed value, or every page in the merge collapses to the same content.
        with patch.object(xml_pipeline, "generate_xml_diagram", return_value=xml_diagram), \
                patch.object(xml_pipeline, "diagnose_xml", return_value=clean), \
                patch.object(xml_pipeline, "repair_xml", side_effect=lambda xml: (xml, clean)), \
                patch.object(xml_pipeline, "_generate_ir_view_page") as view_mock:
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="rename EC2 to Checkout Fn",
                provider="gemini",
                platform="aws",
                logger=logger,
                diagram_renderer="local",
                current_xml=self._CURRENT_XML,
                diagram_views=["network", "logical"],
            ))
        view_mock.assert_not_called()
        self.assertEqual(result["status"], "success")
        logical_page = extract_page_by_name(result["xml"], "Logical")
        self.assertIsNotNone(logical_page)
        self.assertIn("Orders", logical_page)  # untouched pre-edit content
        self.assertIn("Checkout Fn", result["xml"])  # the edit itself still applied
        degraded = {f["feature"]: f for f in result["degraded_features"]}
        self.assertIn("extra views", degraded)
        self.assertIn("Logical", degraded["extra views"]["message"])


class SurgicalEditLibavoidSkipTests(unittest.TestCase):
    """Regression: a live capture measured libavoid edge routing (a real MCP
    round-trip) firing once per page, including a page just carried forward
    byte-for-byte unchanged from current_xml. That page's routing was already
    valid — re-running it wasted ~1.8-2.9s per untouched tab on every single
    surgical edit, the largest cost on this path after the edit-plan LLM call
    itself. Fixed by skipping libavoid for any page name in
    unchanged_page_names (populated by the extra-view carry-forward above)."""

    _CURRENT_XML = (
        '<mxfile host="app.diagrams.net">'
        + _page(
            "Logical",
            '<mxCell id="svc" value="Orders" style="html=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>',
        )
        + _page(
            "Network",
            '<mxCell id="ec2" value="EC2" style="html=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>',
        )
        + "</mxfile>"
    )

    def test_libavoid_only_routes_the_edited_page(self) -> None:
        xml_diagram = XmlDiagram(
            xml=_page("Network", '<mxCell id="ec2" value="Checkout Service" style="html=1;" vertex="1" parent="1">'
                                  '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'),
            assistant_message="ok", suggestions=[], step_markers=[],
        )
        logger = StepLogger()
        _seed_successful_surgical_edit(logger)
        clean = XmlDiagnostics(errors=[], warnings=[])
        create_diagram_mock = AsyncMock(side_effect=lambda xml, **kw: ("http://preview", xml))
        with patch.object(xml_pipeline, "generate_xml_diagram", return_value=xml_diagram), \
                patch.object(xml_pipeline, "diagnose_xml", return_value=clean), \
                patch.object(xml_pipeline, "repair_xml", side_effect=lambda xml: (xml, clean)), \
                patch.object(xml_pipeline, "resolve_shape_styles", AsyncMock(return_value={})), \
                patch.object(xml_pipeline, "create_diagram", create_diagram_mock), \
                patch.object(xml_pipeline, "_generate_ir_view_page") as view_mock:
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="rename EC2 to Checkout Service",
                provider="gemini",
                platform="aws",
                logger=logger,
                diagram_renderer="mcp",
                libavoid=True,
                current_xml=self._CURRENT_XML,
                diagram_views=["network", "logical"],
            ))
        view_mock.assert_not_called()
        self.assertEqual(result["status"], "success")

        def _page_name(page_xml: str) -> str | None:
            doc = ET.fromstring(xml_pipeline.ensure_diagram_document(page_xml, "?"))
            diag = doc.find(".//diagram")
            return diag.get("name") if diag is not None else None

        routed_page_names = {_page_name(call.args[0]) for call in create_diagram_mock.call_args_list}
        self.assertNotIn(
            "Logical", routed_page_names,
            f"libavoid must not re-route the untouched Logical page: {routed_page_names}",
        )
        self.assertEqual(create_diagram_mock.call_count, 1, "libavoid should route only the edited Network page")
        self.assertIn("Checkout Service", result["xml"])


class RefreshOverviewAndLegendPositionsTests(unittest.TestCase):
    """Regression: found via a real live capture where the Overview Panel on
    the Network page stayed stale after a surgical edit grew its container,
    even with the ordering fix in place — the fixer only ever looks at the
    *first* <diagram> page, and Network is page 2 (Logical is page 1 per
    VIEW_ORDER), so re-running it on the merged multi-page mxfile silently
    fixed Logical and no-opped for Network."""

    def test_fixes_a_non_first_page_in_a_multi_page_mxfile(self) -> None:
        logical = _page(
            "Logical",
            '<mxCell id="l_cloud" value="Group" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="400" height="300" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;&lt;div&gt;1. Step.&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="660" y="100" width="400" height="160" as="geometry"/></mxCell>',
        )
        # Network's container was grown by fit_containers_to_children (e.g. a
        # surgical add_node into it) to width=900 — its panel, still at its
        # pre-growth x, now overlaps the container.
        network = _page(
            "Network",
            '<mxCell id="n_cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="900" height="400" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;&lt;div&gt;1. Step.&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="820" y="100" width="400" height="160" as="geometry"/></mxCell>',
        )
        merged = f'<mxfile host="app.diagrams.net">{logical}{network}</mxfile>'

        fixed = xml_pipeline._refresh_overview_and_legend_positions(merged)

        net_page = extract_page_by_name(fixed, "Network")
        self.assertIsNotNone(net_page)
        panel = next(c for c in ET.fromstring(net_page).iter("mxCell") if c.get("id") == "overview_panel")
        geom = panel.find("mxGeometry")
        px, pw = float(geom.get("x")), float(geom.get("width"))
        self.assertGreaterEqual(
            px, 200.0 + 900.0,
            "Network's panel must clear its grown container's right edge — "
            "fixing only page 1 (Logical) silently no-ops for page 2 (Network)",
        )
        self.assertGreater(pw, 0.0)

    def test_single_page_input_is_unaffected(self) -> None:
        xml = (
            '<mxfile host="app.diagrams.net">' + _page(
                "Network",
                '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
                '<mxGeometry x="200" y="100" width="600" height="400" as="geometry"/></mxCell>'
                '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;&lt;div&gt;1. Step.&lt;/div&gt;" '
                'style="rounded=1;html=1;" vertex="1" parent="1">'
                '<mxGeometry x="840" y="100" width="400" height="160" as="geometry"/></mxCell>',
            ) + "</mxfile>"
        )
        fixed = xml_pipeline._refresh_overview_and_legend_positions(xml)
        panel = next(c for c in ET.fromstring(fixed).iter("mxCell") if c.get("id") == "overview_panel")
        self.assertEqual(float(panel.find("mxGeometry").get("x")), 840.0)


if __name__ == "__main__":
    unittest.main()
