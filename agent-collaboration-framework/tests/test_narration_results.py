"""Committed-result projection, audience boundaries and complete information feedback."""

import pytest

from collaboration_framework.contracts import NarrationEvidence
from collaboration_framework.engine import AdjudicationEngineService
from collaboration_framework.engine.models import DomainEvent, EngineRuntimeSnapshot
from collaboration_framework.host.application import (
    ActionPlanNarrationValidationError,
    ActionPlanNarrator,
)
from tests.test_action_plan_orchestrator import orchestrator, plan, player_input
from tests.test_happy_frog_village_v3_fixture import (
    ACTOR,
    PLAYER,
    ROOM,
    initial_state,
    load_module,
)


@pytest.mark.parametrize(
    "case,expected",
    [
        ("new", True),
        ("old", False),
        ("actor", False),
        ("promoted", True),
        ("initial", False),
        ("initial_actor_promoted", True),
        ("hidden", False),
        ("journal", False),
        ("restricted_recipient", False),
        ("hidden_after", False),
    ],
)
def test_public_information_is_a_delta_for_the_broadcast_audience(case, expected):
    module = load_module()
    fact = next(item for item in module.information if item.id == "james_refuses_home")
    if case in {"initial", "initial_actor_promoted"}:
        fact = fact.model_copy(
            update={
                "discovery": fact.discovery.model_copy(
                    update={
                        "initial": "known",
                        "scope": "actor"
                        if case == "initial_actor_promoted"
                        else "party",
                    }
                )
            }
        )
    if case == "hidden":
        fact = fact.model_copy(
            update={
                "audience": fact.audience.model_copy(
                    update={"player_when_discovered": False}
                )
            }
        )
    if case == "journal":
        fact = fact.model_copy(
            update={
                "presentation": fact.presentation.model_copy(
                    update={"channels": ("journal",)}
                )
            }
        )
    module = module.model_copy(
        update={
            "information": tuple(
                fact if item.id == fact.id else item for item in module.information
            )
        }
    )
    before = initial_state()
    if case == "old":
        before = before.model_copy(
            update={"discovered_facts": (*before.discovered_facts, fact.id)}
        )
    if case == "promoted":
        before = before.model_copy(
            update={"actor_discovered_facts": {ACTOR: (fact.id,)}}
        )
    after = before.model_copy(
        update={"discovered_facts": (*before.discovered_facts, fact.id)}
    )
    if case == "actor":
        after = before.model_copy(
            update={"actor_discovered_facts": {ACTOR: (fact.id,)}}
        )
    if case == "hidden_after":
        after = before
    if case == "restricted_recipient":
        # Acting player sees it, but another bound player must not receive it.
        after = after.model_copy(
            update={
                "actors": {
                    **after.actors,
                    "other": after.actors[ACTOR].model_copy(
                        update={"player_id": "other-player"}
                    ),
                },
                "visibility_overrides": {f"actor:other:information:{fact.id}": False},
            }
        )
    runtime = EngineRuntimeSnapshot(
        module_id=module.module_id,
        module_version=module.version,
        module_content=module,
        game_state=before,
        revision="0",
    )
    events = tuple(
        DomainEvent(
            event_id=f"evt-{i}",
            sequence=i,
            type="information.revealed",
            room_id=ROOM,
            actor_id=ACTOR,
            client_action_id="reveal",
            cause="test",
            payload={
                "information_id": fact.id,
                "scope": "actor" if case == "actor" else "party",
            },
        )
        for i in (1, 2)
    )
    evidence = AdjudicationEngineService._narration_evidence(
        runtime, new_state=after, events=events, player_id=PLAYER, actor_id=ACTOR
    )
    assert bool(evidence) is expected
    if expected:
        assert (
            len(evidence) == 1
        )  # repeated events from one execution do not duplicate prose
        assert evidence[0].ref == "evt-1"
        assert evidence[0].description == fact.player_content
        assert fact.keeper_content not in evidence[0].description


@pytest.mark.asyncio
async def test_information_needs_full_body_and_only_source_spans_get_style_exemption():
    service, _, _, _, _ = orchestrator()
    original = player_input()
    await service.start_or_resume(original, plan=plan(2))
    context = await service.build_narration_context(original)
    ref = context.allowed_evidence_refs[0]
    body = "你听见答复：“今晚不能离开；必须等到明天。”"
    fact = NarrationEvidence(
        ref=ref,
        kind="information_revealed",
        subject_id="reply",
        subject_name="离开的条件",
        description=body,
        required_in_narration=True,
    )
    context = context.model_copy(
        update={"narration_evidence": (fact,), "addressing_mode": "named_actor"}
    )
    narrator = ActionPlanNarrator(None)
    for text in [fact.subject_name, body.replace("不能", "可以"), body.split("；")[0]]:
        with pytest.raises(ActionPlanNarrationValidationError, match="安全校验") as exc:
            narrator.validate(context, {"text": text})
        assert exc.value.reason == "required_evidence_missing"
    candidate = {
        "text": body.replace("；", "；\n"),
        "npc_replies": [{"speaker_id": "npc", "text": "..."}],
    }
    output = narrator.validate(context, candidate)
    assert output.claimed_evidence_refs == (ref,)
    with pytest.raises(ActionPlanNarrationValidationError) as exc:
        narrator.validate(context, {**candidate, "text": body + "你继续前进。"})
    assert exc.value.reason == "subject_ownership"
    with pytest.raises(ActionPlanNarrationValidationError) as exc:
        narrator.validate(context, {**candidate, "text": body + "有人喊：“走吧！”"})
    assert exc.value.reason == "npc_dialogue_embedded_in_text"


@pytest.mark.parametrize(
    "event_type,payload,expected",
    [
        (
            "travel.resolved",
            {"destination_id": "room", "path": ["hall", "room"]},
            "room",
        ),
        ("travel.resolved", {"destination_id": "hall", "path": ["hall"]}, None),
        (
            "travel.interrupted",
            {"destination_id": "room", "current_location_id": "hall", "path": ["hall"]},
            None,
        ),
        (
            "travel.interrupted",
            {
                "destination_id": "room",
                "current_location_id": "gate",
                "path": ["hall", "gate"],
            },
            "gate",
        ),
        ("location.entered", {"location_id": "room"}, "room"),
        ("entity.moved", {"entity_id": "companion", "location_id": "room"}, None),
        ("entity.moved", {"entity_id": "key", "holder_actor_id": ACTOR}, "key"),
    ],
)
def test_committed_travel_and_inventory_results_use_actual_effects(
    event_type, payload, expected
):
    from collaboration_framework.engine.persistent_results import (
        committed_results_from_events,
    )

    event = DomainEvent(
        event_id="effect",
        sequence=1,
        type=event_type,
        payload=payload,
        room_id=ROOM,
        actor_id=ACTOR,
        client_action_id="action",
        cause="test",
    )
    results = committed_results_from_events((event,), item_ids=frozenset({"key"}))
    assert [item.target_id for item in results] == (
        [] if expected is None else [expected]
    )
    if results:
        assert results[0].kind == ("inventory" if expected == "key" else "location")


@pytest.mark.asyncio
async def test_confirmed_arrival_requires_place_and_allows_new_scene_atmosphere():
    from collaboration_framework.contracts import CommittedResult
    from collaboration_framework.host.application.narration_policy import (
        narration_atmosphere_rejection_reason,
    )

    service, _, _, _, _ = orchestrator()
    original = player_input()
    await service.start_or_resume(original, plan=plan(2))
    context = await service.build_narration_context(original)
    scene = context.player_view.scene
    prior = "下午的阳光透过庄园会客厅的窗户。人们在桌边等候。"
    text = f"你走进{scene.name}。下午的阳光落在这里的窗台上。"
    assert narration_atmosphere_rejection_reason(text, prior) == "atmosphere_repeat"
    ref = context.allowed_evidence_refs[0]
    step = context.completed_steps[0].model_copy(
        update={
            "committed_results": (
                CommittedResult(kind="location", target_id=scene.id, event_ref=ref),
            )
        }
    )
    context = context.model_copy(
        update={"completed_steps": (step,), "previous_published_narration": prior}
    )
    narrator = ActionPlanNarrator(None)
    assert narrator.validate(context, {"text": text}).text == text
    with pytest.raises(ActionPlanNarrationValidationError) as exc:
        narrator.validate(context, {"text": "你停下脚步。"})
    assert exc.value.reason == "required_arrival_missing"
    assert (
        narration_atmosphere_rejection_reason(prior, prior, scene_changed=True)
        == "atmosphere_repeat"
    )
