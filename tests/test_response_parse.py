"""``estimate_mermaid_node_count`` — the C2 server-side backstop for
simple.py's prompt-only "max 8 nodes" cap (docs/PROMPT_BATTLE_TEST.md).

Deliberately not a real Mermaid parser (there are three allowed grammars:
flowchart, sequenceDiagram, stateDiagram-v2) — a best-effort heuristic feeding
a warning log, not a validation gate. The one property that actually matters
is not overcounting: a heuristic that inflates node counts would make the
warning noise, not signal.
"""

from __future__ import annotations

import unittest

from app.adapters.outbound.llm.response_parse import estimate_mermaid_node_count


class MermaidNodeCountTests(unittest.TestCase):
    def test_flowchart_shapes(self) -> None:
        src = """flowchart LR
            A[Start] --> B(Process)
            B --> C{Decision}
            C -->|Yes| D[End]
            C -->|No| B
        """
        self.assertEqual(estimate_mermaid_node_count(src), 4)

    def test_sequence_diagram_participants(self) -> None:
        src = """sequenceDiagram
            participant User
            participant API
            User->>API: Request
            API-->>User: Response
        """
        self.assertEqual(estimate_mermaid_node_count(src), 2)

    def test_state_diagram_transitions(self) -> None:
        src = """stateDiagram-v2
            [*] --> Idle
            Idle --> Running
            Running --> [*]
        """
        self.assertEqual(estimate_mermaid_node_count(src), 2)

    def test_diagram_type_declaration_is_not_a_node(self) -> None:
        """Regression: an early version's lookahead used `\\s*`, which crosses
        newlines — `stateDiagram-v2` followed (several lines down) by any
        bracket at all made the declaration itself count as a node."""
        src = "stateDiagram-v2\n\n\n    [*] --> Idle\n"
        self.assertEqual(estimate_mermaid_node_count(src), 1)  # Idle only

    def test_large_flowchart_counts_every_node(self) -> None:
        src = "flowchart LR\n" + "\n".join(
            f"N{i}[Node {i}] --> N{i + 1}[Node {i + 1}]" for i in range(10)
        )
        self.assertEqual(estimate_mermaid_node_count(src), 11)

    def test_control_flow_keywords_are_not_counted(self) -> None:
        src = """sequenceDiagram
            participant A
            participant B
            loop Every minute
                A->>B: Ping
            end
            Note over A,B: Idle
        """
        self.assertEqual(estimate_mermaid_node_count(src), 2)

    def test_empty_source_counts_zero(self) -> None:
        self.assertEqual(estimate_mermaid_node_count(""), 0)

    def test_repeated_node_reference_counts_once(self) -> None:
        src = "flowchart TD\nA[Start] --> B[Mid]\nB --> A\n"
        self.assertEqual(estimate_mermaid_node_count(src), 2)
