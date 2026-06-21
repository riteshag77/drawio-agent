"""
3-turn incremental editing smoke test (no LLM):
1. First-pass JSON layout -> XML
2. Add a node via ID-based operation
3. Delete a container (cascade)

Verifies unchanged cells stay byte-for-byte identical after each edit.
"""

from __future__ import annotations

import re
import unittest

from app.utils.chat import is_minimal_diagram
from app.diagram.draftsman import generate_xml
from app.models import ArchitectureDiagram, DiagramOperation, Edge, Node
from app.diagram.xml_tools import apply_diagram_operations, validate_and_fix_xml, wrap_with_mxfile


def _extract_mxcell(xml: str, cell_id: str) -> str | None:
    pattern = re.compile(
        rf'(<mxCell\b[^>]*\bid="{re.escape(cell_id)}"[^>]*>.*?</mxCell>|<mxCell\b[^>]*\bid="{re.escape(cell_id)}"[^>]*/>)',
        re.DOTALL,
    )
    m = pattern.search(xml)
    return m.group(1) if m else None


def _make_sample_architecture() -> ArchitectureDiagram:
    return ArchitectureDiagram(
        nodes=[
            Node(
                id="cloud",
                label="AWS Cloud",
                shape_type="aws_cloud",
                is_container=True,
            ),
            Node(
                id="users",
                label="Users",
                shape_type="user_client",
                parent_id="cloud",
            ),
            Node(
                id="apigw",
                label="API Gateway",
                shape_type="aws_api_gateway",
                parent_id="cloud",
            ),
        ],
        edges=[
            Edge(source_id="users", target_id="apigw", label=""),
        ],
        suggestions=[],
    )


class MultiturnEditingTest(unittest.TestCase):
    def test_three_turn_incremental_editing_preserves_unchanged_cells(self):
        # Turn 1: deterministic first-pass layout
        arch = _make_sample_architecture()
        xml_turn1 = generate_xml(arch, "aws")
        self.assertFalse(is_minimal_diagram(xml_turn1))

        users_before = _extract_mxcell(xml_turn1, "users")
        apigw_before = _extract_mxcell(xml_turn1, "apigw")
        self.assertIsNotNone(users_before)
        self.assertIsNotNone(apigw_before)

        # Turn 2: add Lambda node
        add_op = DiagramOperation(
            operation="add",
            cell_id="lambda1",
            new_xml=(
                '<mxCell id="lambda1" value="Lambda" '
                'style="shape=mxgraph.aws4.lambda;fillColor=#ED7100;strokeColor=#ffffff;" '
                'vertex="1" parent="cloud">'
                '<mxGeometry x="500" y="80" width="55" height="55" as="geometry"/>'
                "</mxCell>"
            ),
        )
        turn2 = apply_diagram_operations(xml_turn1, [add_op])
        self.assertEqual(turn2.errors, [])
        xml_turn2 = validate_and_fix_xml(turn2.result).fixed or turn2.result

        self.assertIn('id="lambda1"', xml_turn2)
        self.assertEqual(_extract_mxcell(xml_turn2, "users"), users_before)
        self.assertEqual(_extract_mxcell(xml_turn2, "apigw"), apigw_before)

        # Turn 3: delete container cloud (cascade children)
        delete_op = DiagramOperation(operation="delete", cell_id="cloud", new_xml=None)
        turn3 = apply_diagram_operations(xml_turn2, [delete_op])
        self.assertEqual(turn3.errors, [])
        xml_turn3 = validate_and_fix_xml(turn3.result).fixed or turn3.result

        self.assertNotIn('id="cloud"', xml_turn3)
        self.assertNotIn('id="users"', xml_turn3)
        self.assertNotIn('id="apigw"', xml_turn3)
        self.assertNotIn('id="lambda1"', xml_turn3)

    def test_cached_wrap_and_minimal_detection(self):
        from app.patterns.cache import find_cached_response

        prompt = (
            "Highly available web app with API Gateway, Fargate in public/private subnets, "
            "Aurora, ElastiCache, CloudFront, Route 53, WAF, and CloudWatch"
        )
        cached = find_cached_response(prompt, has_image=False)
        self.assertIsNotNone(cached)
        wrapped = wrap_with_mxfile(cached.xml, "Amazon Web Services")
        self.assertFalse(is_minimal_diagram(wrapped))
        self.assertIn('id="cloud"', wrapped)


if __name__ == "__main__":
    unittest.main()
