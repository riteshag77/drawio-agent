"""Route one spoken utterance to a UI command, a diagram turn, or a question.

Only reached when the browser's exact-phrase registry found no match, which is
most natural speech: the registry answers "stop" and "clear chat" in zero
milliseconds, and this handles "can you make that database multi-AZ".

Two jobs, deliberately in one call rather than two:

1. **Route** the utterance. A voice user has no keyboard and no menu, so the
   same sentence has to be able to mean "change a setting" or "change the
   diagram" without a prefix to disambiguate it.
2. **Normalize the transcript.** Browser speech recognition is weak on cloud
   vocabulary — "EC2" comes back as "easy to", "VPC" as "V P C" — and a
   diagram prompt carrying those is worse than useless. The corrected text
   rides back on the same response, so fixing it costs no extra round-trip.

The prompt text arrives as a parameter rather than being imported: prompt packs
live in the adapter layer, and core reaching into adapters is a boundary this
package does not cross (see ``[tool.importlinter]`` in pyproject.toml).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from app.core.ports.llm import LlmPort, get_llm

if TYPE_CHECKING:
    from app.core.logging import StepLogger

#: Cap on the command vocabulary accepted from the client. The registry is ~60
#: entries; this bounds prompt size against a malformed or hostile payload
#: without ever being reached in normal use.
MAX_COMMANDS = 200


class VoiceCommand(BaseModel):
    """One entry of the browser's command registry, as the client reports it."""

    id: str = Field(max_length=80)
    label: str = Field(default="", max_length=160)
    sample: str = Field(default="", max_length=200)


class VoiceRoute(BaseModel):
    """What the app should do with an utterance."""

    kind: Literal["ui_command", "diagram_turn", "clarify"] = Field(
        description=(
            "ui_command = run one of the listed commands; "
            "diagram_turn = send `prompt` to the diagram pipeline; "
            "clarify = the utterance was unclear, say `speak` and do nothing."
        ),
    )
    command_id: str = Field(
        default="",
        max_length=80,
        description="Required when kind is ui_command; must be one of the listed ids.",
    )
    prompt: str = Field(
        default="",
        max_length=4000,
        description=(
            "Required when kind is diagram_turn: the utterance rewritten as a "
            "diagram instruction, with cloud terms spelled correctly."
        ),
    )
    speak: str = Field(
        default="",
        max_length=300,
        description="One short sentence to read aloud. Spoken, so no markup or ids.",
    )


def _fallback(transcript: str, has_diagram: bool) -> VoiceRoute:
    """What to do when the router cannot answer.

    Fail *open*, never silent: the user said something out loud and the one
    unacceptable outcome is that it vanishes. With a diagram on screen the
    likeliest intent by far is an edit, so the raw transcript goes through as
    one; with an empty canvas there is nothing to edit against, so it becomes a
    question the UI can put in the composer for the user to confirm.
    """
    text = (transcript or "").strip()
    if has_diagram and text:
        return VoiceRoute(kind="diagram_turn", prompt=text, speak="Applying that change.")
    return VoiceRoute(
        kind="clarify",
        speak="I didn't quite catch that — I've put it in the box so you can check it.",
    )


def build_voice_route_request(
    transcript: str,
    *,
    commands: list[VoiceCommand],
    has_diagram: bool,
    platform: str = "",
    diagram_type: str = "",
) -> str:
    """The human half of the router call. Pure string work — no I/O."""
    lines = [f'UTTERANCE: "{(transcript or "").strip()}"', ""]
    lines.append("CONTEXT:")
    lines.append(f"- a diagram is currently on screen: {'yes' if has_diagram else 'no'}")
    if platform:
        lines.append(f"- target cloud platform: {platform}")
    if diagram_type:
        lines.append(f"- current diagram type: {diagram_type}")
    lines.append("")
    lines.append("AVAILABLE UI COMMANDS (use the exact id):")
    for cmd in commands[:MAX_COMMANDS]:
        sample = f' e.g. "{cmd.sample}"' if cmd.sample else ""
        lines.append(f"- {cmd.id}: {cmd.label}{sample}")
    return "\n".join(lines)


def route_voice_utterance(
    system_prompt: str,
    transcript: str,
    *,
    commands: list[VoiceCommand],
    has_diagram: bool,
    platform: str = "",
    diagram_type: str = "",
    provider: str = "",
    logger: "StepLogger | None" = None,
    llm: LlmPort | None = None,
) -> VoiceRoute:
    """Route one utterance, never raising.

    A router failure must degrade to *something the user can act on*, because
    the alternative — an exception surfacing as an error toast — leaves a
    hands-free user with no way to recover.
    """
    text = (transcript or "").strip()
    if not text:
        return VoiceRoute(kind="clarify", speak="I didn't hear anything.")

    human = build_voice_route_request(
        text,
        commands=commands,
        has_diagram=has_diagram,
        platform=platform,
        diagram_type=diagram_type,
    )
    _llm = llm or get_llm()
    try:
        route = _llm(system_prompt, human, provider, VoiceRoute, logger, None)
    except Exception as exc:  # noqa: BLE001 - degraded routing beats a dead mic
        if logger:
            logger.log("voice", f"Voice routing failed: {exc}", level="warning")
        return _fallback(text, has_diagram)

    return _sanitize(route, text, commands=commands, has_diagram=has_diagram)


def _sanitize(
    route: VoiceRoute,
    transcript: str,
    *,
    commands: list[VoiceCommand],
    has_diagram: bool,
) -> VoiceRoute:
    """Hold the model to the contract before the browser acts on it.

    The client runs whatever ``command_id`` comes back, so an invented one
    would be a silent no-op at best. Checking it here keeps that check in one
    place rather than duplicated in JS.
    """
    if route is None:
        return _fallback(transcript, has_diagram)

    if route.kind == "ui_command":
        known = {c.id for c in commands}
        if route.command_id not in known:
            # It wanted to *do* something rather than draw, but named a command
            # that does not exist — treat as unclear, not as a diagram edit,
            # since guessing an edit here would change the canvas unasked.
            return VoiceRoute(
                kind="clarify",
                speak="I'm not sure how to do that — say “help” for what I can do.",
            )
        return route

    if route.kind == "diagram_turn":
        if not route.prompt.strip():
            route.prompt = transcript
        if not has_diagram and not route.prompt.strip():
            return _fallback(transcript, has_diagram)
        return route

    if not route.speak.strip():
        route.speak = "Sorry, I didn't catch that."
    return route
