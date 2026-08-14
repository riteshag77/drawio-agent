"""SaaS vendor mismatch detector — the wrong-brand-logo bug found live in the
prompt battle test (see config/references/saas.txt's own warning about it):
a leaf whose shape_type names a real curated vendor (e.g. saas_fis) but whose
label doesn't match that vendor at all (e.g. "Clearinghouse A") renders the
wrong company's logo on the diagram.

Mirrors test_governance.py's convention: real domain IR objects, no LLM, no DB.
"""

from __future__ import annotations

import unittest

from app.adapters.outbound.saas_catalog import find_saas_vendor_mismatches
from app.domain.ir.base import Leaf
from app.domain.ir.corridor import AnchoredCorridor, Corridor
from app.domain.ir.security import SecurityIR


def _security_ir(saas_leaf: Leaf) -> SecurityIR:
    return SecurityIR(
        corridors=[
            Corridor(id="zone_public", label="Public", leaves=[
                Leaf(id="waf", shape_type="aws_waf", label="WAF"),
            ]),
            Corridor(id="zone_restricted", label="Restricted", leaves=[
                Leaf(id="db", shape_type="aws_rds", label="DB"),
            ]),
        ],
        anchored=[
            AnchoredCorridor(id="zone_external_saas", label="External SaaS", anchor_id="zone_restricted", leaves=[saas_leaf]),
        ],
        purpose="Test diagram.",
    )


class SaasVendorMismatchTests(unittest.TestCase):
    def test_flags_a_real_vendor_key_on_an_unrelated_label(self):
        ir = _security_ir(Leaf(id="ch_a", shape_type="saas_fis", label="Clearinghouse A"))
        warnings = find_saas_vendor_mismatches(ir)
        self.assertEqual(len(warnings), 1)
        self.assertIn("ch_a", warnings[0])
        self.assertIn("saas_fis", warnings[0])

    def test_passes_when_label_names_the_real_vendor(self):
        ir = _security_ir(Leaf(id="fis1", shape_type="saas_fis", label="FIS Core Banking"))
        self.assertEqual(find_saas_vendor_mismatches(ir), [])

    def test_passes_a_functional_label_that_still_mentions_the_vendor(self):
        ir = _security_ir(Leaf(id="md", shape_type="saas_bloomberg", label="Market Data (Bloomberg)"))
        self.assertEqual(find_saas_vendor_mismatches(ir), [])

    def test_never_flags_an_invented_novel_key(self):
        """An unresolved key (not a real curated vendor) is the *correct*
        answer for an unnamed vendor — never a mismatch, whatever the label."""
        ir = _security_ir(Leaf(id="ch_a", shape_type="saas_clearinghouse_a", label="Clearinghouse A"))
        self.assertEqual(find_saas_vendor_mismatches(ir), [])

    def test_never_flags_a_non_saas_shape_type(self):
        ir = _security_ir(Leaf(id="other_db", shape_type="aws_rds", label="Whatever"))
        self.assertEqual(find_saas_vendor_mismatches(ir), [])

    def test_archimate_ir_has_no_corridors_and_is_skipped_cleanly(self):
        class _FakeArchimateIR:
            pass  # no all_corridors() — matches ArchimateIR's actual shape

        self.assertEqual(find_saas_vendor_mismatches(_FakeArchimateIR()), [])


if __name__ == "__main__":
    unittest.main()
