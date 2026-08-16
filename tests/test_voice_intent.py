"""Voice-mode intent routing.

The property that matters is not accuracy — that is the model's job — but that
a hands-free user is never left with nothing. Somebody who has just spoken
aloud cannot read an error toast or click retry, so every failure mode here has
to degrade into something the UI can still act on.
"""

from __future__ import annotations

import unittest

from app.core.services.voice_intent import (
    VoiceCommand,
    VoiceRoute,
    build_voice_route_request,
    route_voice_utterance,
)

_COMMANDS = [
    VoiceCommand(id="cost-on", label="Enable cost estimate", sample="enable cost"),
    VoiceCommand(id="insights-logs", label="Insights: Logs", sample="logs tab"),
    VoiceCommand(id="download-png", label="Export PNG", sample="export png"),
]


def _llm_returning(route: VoiceRoute):
    def _fake(system_prompt, human, provider, output_type, logger, attachments):
        _fake.calls.append({"system": system_prompt, "human": human, "provider": provider})
        return route
    _fake.calls = []
    return _fake


def _llm_raising(exc: Exception):
    def _fake(*_args, **_kwargs):
        raise exc
    return _fake


class RoutingTests(unittest.TestCase):
    def test_a_ui_command_is_passed_through(self) -> None:
        route = route_voice_utterance(
            "SYS", "turn on the cost estimate please",
            commands=_COMMANDS, has_diagram=True,
            llm=_llm_returning(VoiceRoute(
                kind="ui_command", command_id="cost-on", speak="Cost estimation on.",
            )),
        )
        self.assertEqual((route.kind, route.command_id), ("ui_command", "cost-on"))

    def test_a_diagram_turn_carries_its_prompt(self) -> None:
        route = route_voice_utterance(
            "SYS", "add an easy two instance behind the load balancer",
            commands=_COMMANDS, has_diagram=True,
            llm=_llm_returning(VoiceRoute(
                kind="diagram_turn",
                prompt="Add an EC2 instance behind the load balancer",
                speak="Adding an EC2 instance.",
            )),
        )
        self.assertEqual(route.kind, "diagram_turn")
        self.assertIn("EC2", route.prompt)

    def test_an_invented_command_id_becomes_a_question_not_an_edit(self) -> None:
        """The client runs whatever id comes back. Guessing an *edit* instead
        would change the canvas the user never asked to change, so an unknown
        command has to degrade to asking, not to drawing."""
        route = route_voice_utterance(
            "SYS", "do the thing",
            commands=_COMMANDS, has_diagram=True,
            llm=_llm_returning(VoiceRoute(kind="ui_command", command_id="not-a-command")),
        )
        self.assertEqual(route.kind, "clarify")
        self.assertTrue(route.speak)

    def test_a_diagram_turn_with_no_prompt_falls_back_to_the_transcript(self) -> None:
        route = route_voice_utterance(
            "SYS", "make the database multi a z",
            commands=_COMMANDS, has_diagram=True,
            llm=_llm_returning(VoiceRoute(kind="diagram_turn", prompt="")),
        )
        self.assertEqual(route.kind, "diagram_turn")
        self.assertEqual(route.prompt, "make the database multi a z")

    def test_clarify_always_has_something_to_say(self) -> None:
        """`speak` is the only output a hands-free user perceives — a silent
        clarify is indistinguishable from the app having crashed."""
        route = route_voice_utterance(
            "SYS", "mumble",
            commands=_COMMANDS, has_diagram=False,
            llm=_llm_returning(VoiceRoute(kind="clarify", speak="")),
        )
        self.assertTrue(route.speak.strip())


class FailOpenTests(unittest.TestCase):
    def test_an_llm_failure_with_a_diagram_still_attempts_the_edit(self) -> None:
        route = route_voice_utterance(
            "SYS", "add a redis cache",
            commands=_COMMANDS, has_diagram=True,
            llm=_llm_raising(RuntimeError("provider exploded")),
        )
        self.assertEqual(route.kind, "diagram_turn")
        self.assertEqual(route.prompt, "add a redis cache")

    def test_an_llm_failure_with_no_diagram_asks_rather_than_builds(self) -> None:
        """With an empty canvas there is nothing to edit against, and firing a
        30-second fresh generation off an unrouted transcript is the expensive
        way to be wrong."""
        route = route_voice_utterance(
            "SYS", "something unclear",
            commands=_COMMANDS, has_diagram=False,
            llm=_llm_raising(RuntimeError("provider exploded")),
        )
        self.assertEqual(route.kind, "clarify")
        self.assertTrue(route.speak.strip())

    def test_an_empty_transcript_never_calls_the_model(self) -> None:
        llm = _llm_returning(VoiceRoute(kind="clarify"))
        route = route_voice_utterance(
            "SYS", "   ", commands=_COMMANDS, has_diagram=True, llm=llm,
        )
        self.assertEqual(route.kind, "clarify")
        self.assertEqual(llm.calls, [])


class PromptAssemblyTests(unittest.TestCase):
    def test_every_command_id_reaches_the_model(self) -> None:
        """The model can only return an id it was shown, and the sanitizer
        rejects any it was not — so a dropped id silently removes a command
        from what voice can reach."""
        human = build_voice_route_request(
            "anything", commands=_COMMANDS, has_diagram=True,
        )
        for cmd in _COMMANDS:
            self.assertIn(cmd.id, human)

    def test_screen_state_is_stated_explicitly(self) -> None:
        with_diagram = build_voice_route_request("x", commands=[], has_diagram=True)
        without = build_voice_route_request("x", commands=[], has_diagram=False)
        self.assertIn("yes", with_diagram)
        self.assertIn("no", without)
        self.assertNotEqual(with_diagram, without)

    def test_the_command_list_is_bounded(self) -> None:
        many = [VoiceCommand(id=f"cmd-{i}") for i in range(500)]
        human = build_voice_route_request("x", commands=many, has_diagram=False)
        self.assertIn("cmd-0", human)
        self.assertNotIn("cmd-400", human)


if __name__ == "__main__":
    unittest.main()
