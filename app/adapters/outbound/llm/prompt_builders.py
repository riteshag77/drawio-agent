"""Display labels for the cloud platforms.

All that remains of what was the Mermaid path's human-prompt assembly. That
path emitted Mermaid source for the draw.io embed to import, which the embed
has never had an action for; it now generates a ``SimpleDiagramIR`` through the
same LLM-emits-IR machinery as every other view, so its prompt builder
(``build_chat_prompt``) and the two chat-trimming helpers only it called went
with it. The trimming primitives themselves still live in
``adapters/outbound/utils/chat.py``, where the surviving builders use them.
"""

from __future__ import annotations

PLATFORM_LABELS = {
    "aws": "Amazon Web Services (AWS)",
    "gcp": "Google Cloud Platform (GCP)",
    "azure": "Microsoft Azure",
    "multi": "Multi-Cloud / Hybrid",
}
