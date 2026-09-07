"""Production model adapter → rules → persistent companions, without keyword routing."""

from pathlib import Path

import pytest
from collaboration_framework.contracts import (
    ActionAdjudication,
    ActionMethod,
    ActionPlanStep,
    ActionTarget,
    EnterLocationEffect,
    ModuleContentV3,
    NarrativeOnlyEffect,
    NoAdjudicationCheck,
    PlayerInput,
    PlayerViewScope,
    SubmitAdjudicationRequest,
)
from collaboration_framework.engine import (
    ActorState,
    AdjudicationEngineService,
    InMemoryEngineStore,
    RuleEngineService,
)
from collaboration_framework.engine.initialization import create_initial_game_state
from collaboration_framework.host.application import PlayerViewProjector
from collaboration_framework.host.schemas import ActionPlanStepContext, MemoryContext

from app.adapters.openai_models import PromptActionPlanStepAdjudicator
from app.core.action_plan_turn import (
    _ModelStepAdjudicator,
    build_action_plan_turn_application,
    build_rule_once_adjudication,
)
from app.core.config import Settings

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "agent-collaboration-framework/docs/module-parser/examples/module-content-validation"
    / "幸福蛙蛙村/module-content-v3.json"
)


def runtime(*, accompanying=False):
    content = ModuleContentV3.model_validate_json(FIXTURE.read_text())
    state = create_initial_game_state(
        content,
        room_id="companions",
        actors={
            "actor": ActorState(
                player_id="player",
                name="调查员",
                source_character_id="character",
                source_character_version=1,
                state={"skills": {}, "attributes": {"STR": 50}},
            ),
        },
    )
    state = state.model_copy(update={"scene_id": "resort_reception"}, deep=True)
    state.entities["james"]["accompanying"] = accompanying
    store = InMemoryEngineStore()
    store.register_room(module_content=content, initial_state=state)
    return store, RuleEngineService(store), AdjudicationEngineService(store)


async def context_for(rules, utterance, request_id="action", kind="travel"):
    player = PlayerInput(
        room_id="companions",
        player_id="player",
        actor_id="actor",
        client_action_id=request_id,
        utterance=utterance,
    )
    projector = PlayerViewProjector(rules)
    view = await projector.project(player)
    return ActionPlanStepContext(
        player_input=player,
        player_view=view,
        keeper_capabilities=await projector.keeper_capabilities(
            player, expected_revision=view.revision
        ),
        plan_id="plan",
        plan_goal=utterance,
        step_index=0,
        step_request_id=request_id,
        step=ActionPlanStep(kind=kind, semantic_goal=utterance),
    )


def travel(context, destination):
    return ActionAdjudication(
        request_id=context.step_request_id,
        source_revision=context.player_view.revision,
        actor_id=context.player_input.actor_id,
        summary=context.step.semantic_goal,
        target=ActionTarget(kind="location", id=destination),
        method=ActionMethod(family="travel", description=context.step.semantic_goal),
        persistence_intent="location",
        check=NoAdjudicationCheck(),
        success_effects=(EnterLocationEffect(location_id=destination),),
    )


class RecordingClient:
    def __init__(self, decide):
        self.decide = decide
        self.calls = []

    async def generate(self, *, schema_name, schema, instructions, input_payload):
        self.calls.append(input_payload)
        assert schema_name == "trpg_action_plan_step_adjudication"
        assert "accompanying" in instructions
        context = ActionPlanStepContext.model_validate(input_payload)
        return self.decide(context).to_json_dict()


@pytest.mark.parametrize("destination", ["guest_room", "staff_area"])
async def test_model_travel_keeps_one_authoritative_companion_move(destination):
    store, rules, engine = runtime(accompanying=True)
    context = await context_for(
        rules, f"带詹姆斯进入{'客房' if destination == 'guest_room' else '员工区'}"
    )
    client = RecordingClient(lambda ctx: travel(ctx, destination))
    decision = await _ModelStepAdjudicator(PromptActionPlanStepAdjudicator(client)).adjudicate(
        context
    )
    assert len(client.calls) == 1  # The former deterministic travel path would skip this.
    assert [effect.type for effect in decision.success_effects] == ["enter_location"]
    await engine.submit(
        SubmitAdjudicationRequest(room_id="companions", player_id="player", adjudication=decision)
    )
    state = store.inspect_state("companions")
    actual = "guest_room" if destination == "guest_room" else "resort_reception"
    assert state.scene_id == actual
    assert state.entities["james"].get("location_id", "resort_reception") == actual
    moves = [
        event for event in store.inspect_domain_events("companions") if event.type == "entity.moved"
    ]
    assert len(moves) == (1 if destination == "guest_room" else 0)
    if moves:
        assert moves[0].payload["reason"] == "accompanying"


async def test_negated_travel_reaches_model_and_does_not_move():
    store, rules, engine = runtime(accompanying=True)
    context = await context_for(rules, "不要带詹姆斯进入客房")

    def decline(ctx):
        return travel(ctx, "guest_room").model_copy(
            update={
                "target": ActionTarget(kind="location", id="resort_reception"),
                "method": ActionMethod(family="action", description="留在原地"),
                "persistence_intent": "none",
                "success_effects": (NarrativeOnlyEffect(),),
            }
        )

    client = RecordingClient(decline)
    decision = await _ModelStepAdjudicator(PromptActionPlanStepAdjudicator(client)).adjudicate(
        context
    )
    assert len(client.calls) == 1
    await engine.submit(
        SubmitAdjudicationRequest(room_id="companions", player_id="player", adjudication=decision)
    )
    assert store.inspect_state("companions").scene_id == "resort_reception"
    assert not any(
        event.type == "entity.moved" for event in store.inspect_domain_events("companions")
    )


class EmptyMemory:
    async def read_context(self, **kwargs):
        return MemoryContext(
            room_id=kwargs["room_id"],
            player_id=kwargs["player_id"],
            actor_id=kwargs["actor_id"],
            as_of_revision=kwargs["revision"],
        )


async def test_production_plan_establishes_companion_by_rule_then_travels_with_fresh_view():
    store, rules, engine = runtime()

    class SemanticClient:
        def __init__(self):
            self.revisions = []

        async def generate(self, *, schema_name, schema, instructions, input_payload):
            if schema_name == "trpg_action_plan_narration":
                assert "keeper_capabilities" not in input_payload
                return {
                    "kind": "narration",
                    "text": "你站定，稍作停顿。",
                    "claimed_evidence_refs": [],
                    "suggested_actions": [],
                }
            if schema_name == "trpg_turn_plan":
                assert "keeper_capabilities" not in input_payload
                return {
                    "kind": "action_plan",
                    "goal": "尝试带詹姆斯去客房",
                    "steps": [
                        {"kind": "travel", "semantic_goal": "尝试扛起詹姆斯同行"},
                        {"kind": "travel", "semantic_goal": "扶着詹姆斯去客房"},
                    ],
                }
            assert schema_name == "trpg_action_plan_step_adjudication"
            ctx = ActionPlanStepContext.model_validate(input_payload)
            self.revisions.append(ctx.player_view.revision)
            assert ctx.keeper_capabilities is not None
            if ctx.step_index == 0:
                # Even kind=travel can select a character-state rule.
                return build_rule_once_adjudication(
                    player_input=ctx.player_input,
                    player_view=ctx.player_view,
                    capabilities=ctx.keeper_capabilities,
                    rule_id="carry_james_against_his_will",
                    option_id="carry-james",
                    summary=ctx.step.semantic_goal,
                ).to_json_dict()
            npc = next(npc for npc in ctx.player_view.scene.visible_entities if npc.id == "james")
            assert any(
                value.key == "accompanying" and value.value is True
                for value in npc.observable_state
            )
            return travel(ctx, "guest_room").to_json_dict()

    client = SemanticClient()
    app = build_action_plan_turn_application(
        store=store,
        engine=rules,
        adjudication_engine=engine,
        settings=Settings(host_model_provider="deepseek", deepseek_api_key="offline-test"),
        client=client,
        planner_client=client,
        memory_source=EmptyMemory(),
    )
    result = await app.start(
        room_id="companions",
        player_id="player",
        client_action_id="semantic-companion",
        utterance="尝试扛起詹姆斯，再扶着他去客房",
    )
    assert result.status in {"awaiting_narration", "completed"}
    assert len(client.revisions) == 2
    assert client.revisions[0] != client.revisions[1]
    state = store.inspect_state("companions")
    assert state.scene_id == "guest_room"
    assert state.entities["james"]["location_id"] == "guest_room"
    assert state.entities["james"]["accompanying"] is True
    view = await rules.read(
        PlayerViewScope(room_id="companions", player_id="player", actor_id="actor")
    )
    assert any(npc.id == "james" for npc in view.scene.visible_entities)
