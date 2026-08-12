"""A4: Use-case layer — high-level orchestration decoupled from HTTP transport.

Replaces direct pipeline calls in routes.py with clean use-case functions.
Routes only handles HTTP parsing/response encoding; use-cases handle business logic.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, AsyncIterator, Optional

from app.adapters.outbound.config.config import get_config
from app.adapters.outbound.llm import providers as providers_module
from app.adapters.outbound.observability import DiagramGenerationTracer
from app.adapters.outbound.repositories import UnifiedRepository
from app.core.logging import StepLogger
from app.core.ports.llm import get_llm
from app.core.services.agent import generate_mermaid_diagram
from app.core.services.intent import classify_intent
from app.core.services.xml_pipeline import run_xml_pipeline

if TYPE_CHECKING:
    from app.adapters.inbound.web.schemas import ChatRequest


class ChatStreamUseCase:
    """High-level use-case for chat-stream diagram generation.
    
    Orchestrates the full pipeline:
    - Intent classification
    - Mermaid vs. full IR path
    - Cost/compliance/well-architected assessment
    - Response formatting (use-case owns the structure, not the transport)
    
    Routes.py calls this and only handles SSE encoding.
    """

    def __init__(
        self,
        repository: UnifiedRepository,
        llm_client: Optional[object] = None,
    ):
        self.repository = repository
        self.llm_client = llm_client
        self.config = get_config()

    async def run_chat_stream(
        self,
        request: ChatRequest,
        tracer: Optional[DiagramGenerationTracer] = None,
    ) -> AsyncIterator[ChatStreamResponse]:
        """Stream diagram generation with step-by-step updates.
        
        Args:
            request: Chat request (text, attachments, provider, etc.)
            tracer: Optional tracer for observability
        
        Yields:
            ChatStreamResponse objects with step logs, intermediate XML, etc.
        """
        if tracer is None:
            tracer = DiagramGenerationTracer(request_id=request.request_id or "unknown")

        logger = StepLogger(request_id=tracer.request_id)

        try:
            # Classify intent (simple Mermaid vs. full cloud IR)
            async with tracer.span("intent.classify"):
                intent = await classify_intent(request.text, logger)

            logger.log("intent", f"Classified as: {intent}", level="info")

            # Route to appropriate path
            if intent == "simple":
                async for response in self._mermaid_path(request, logger, tracer):
                    yield response
            else:
                async for response in self._full_pipeline_path(request, logger, tracer):
                    yield response

        except asyncio.TimeoutError as exc:
            logger.log("error", f"Request timeout after {self.config.request_timeout_seconds}s")
            yield ChatStreamResponse(
                step_log={"step": "error", "message": "Request timeout", "level": "error"},
                status="error",
                error=str(exc),
            )

        except Exception as exc:
            logger.log("error", f"Unexpected error: {exc}")
            yield ChatStreamResponse(
                step_log={"step": "error", "message": str(exc), "level": "error"},
                status="error",
                error=str(exc),
            )

    async def _mermaid_path(
        self,
        request: ChatRequest,
        logger: StepLogger,
        tracer: DiagramGenerationTracer,
    ) -> AsyncIterator[ChatStreamResponse]:
        """Lightweight Mermaid diagram path."""
        async with tracer.span("mermaid.generate"):
            mermaid_diagram = await generate_mermaid_diagram(
                request.text,
                logger=logger,
            )

        # Note: mermaid_diagram is a MermaidDiagram object
        # Yield final response
        yield ChatStreamResponse(
            step_log={"step": "complete", "message": "Mermaid diagram generated", "level": "info"},
            intermediate_xml=mermaid_diagram.xml if hasattr(mermaid_diagram, "xml") else "",
            status="success",
        )

    async def _full_pipeline_path(
        self,
        request: ChatRequest,
        logger: StepLogger,
        tracer: DiagramGenerationTracer,
    ) -> AsyncIterator[ChatStreamResponse]:
        """Full IR + compiler + cost/compliance path."""
        async with tracer.span("pipeline.run"):
            # Run the full XML pipeline
            response = await run_xml_pipeline(
                request=request,
                logger=logger,
                tracer=tracer,
            )

        # Yield final response with cost/compliance/well-architected
        yield ChatStreamResponse(
            step_log={"step": "complete", "message": "Diagram generated", "level": "info"},
            intermediate_xml=response.get("xml", ""),
            cost_estimate=response.get("cost"),
            compliance_assessment=response.get("compliance"),
            well_architected=response.get("well_architected"),
            status="success",
        )


class ChatStreamResponse:
    """Response object yielded by chat stream use-case.
    
    Routes.py encodes this as SSE JSON. Contains:
    - step_log: Current step information
    - intermediate_xml: Current diagram XML
    - cost/compliance/well-architected: Final assessments
    - status: 'in-progress', 'success', 'error'
    - error: Error message if status='error'
    """

    def __init__(
        self,
        step_log: dict,
        intermediate_xml: str = "",
        cost_estimate: Optional[dict] = None,
        compliance_assessment: Optional[dict] = None,
        well_architected: Optional[dict] = None,
        status: str = "in-progress",
        error: Optional[str] = None,
    ):
        self.step_log = step_log
        self.intermediate_xml = intermediate_xml
        self.cost_estimate = cost_estimate
        self.compliance_assessment = compliance_assessment
        self.well_architected = well_architected
        self.status = status
        self.error = error

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "step_log": self.step_log,
            "intermediate_xml": self.intermediate_xml,
            "cost_estimate": self.cost_estimate,
            "compliance_assessment": self.compliance_assessment,
            "well_architected": self.well_architected,
            "status": self.status,
            "error": self.error,
        }


class GenerateDiagramUseCase:
    """High-level use-case for one-shot diagram generation (POST /generate).
    
    Synchronous (no streaming) version for batch/CLI usage.
    """

    def __init__(self, repository: UnifiedRepository):
        self.repository = repository
        self.config = get_config()

    async def generate_diagram(
        self,
        text: str,
        provider: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> dict:
        """Generate a single diagram and return final XML."""
        logger = StepLogger(request_id=request_id)
        tracer = DiagramGenerationTracer(request_id=request_id or "unknown")

        # Classify intent
        intent = await classify_intent(text, logger)

        if intent == "simple":
            # Mermaid path
            async with tracer.span("mermaid.generate"):
                diagram = await generate_mermaid_diagram(text, logger=logger)
                return {"xml": diagram.xml if hasattr(diagram, "xml") else ""}

        # Full pipeline
        from app.adapters.inbound.web.schemas import ChatRequest
        request = ChatRequest(text=text, provider=provider, request_id=request_id)

        async with tracer.span("pipeline.run"):
            response = await run_xml_pipeline(request=request, logger=logger, tracer=tracer)

        return response
