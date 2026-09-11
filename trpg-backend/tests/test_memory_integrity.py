"""End-to-end regressions for the memory integrity audit, using isolated storage."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.models.event import Event, EventAudience
from app.models.memory import MemoryEntryRecord
from app.models.room import Player
from tests.test_memory_system import _create_memory_room


def dialogue(room, player, *, text, speaker="npc", listeners=(), visibility="public", offset=0):
    return Event(
        id=str(uuid.uuid4()),
        room_id=room.id,
        player_id=player.id,
        actor_id=speaker,
        event_type="dialogue.player" if listeners else "dialogue.npc",
        visibility=visibility,
        scene_id="old-scene",
        view_revision="1",
        payload={
            "utterance" if listeners else "text": text,
            "speakerId": speaker,
            "listenerIds": list(listeners),
            "participantIds": [speaker, *listeners],
        },
        created_at=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(seconds=offset),
    )


async def read(memory_store, room, player, actor, **kwargs):
    return await memory_store.read_npc_context(
        room_id=room.id,
        player_id=player.id,
        actor_id=actor,
        revision="100",
        entity_ids=("npc",),
        location_id="new-scene",
        **kwargs,
    )


async def test_valid_long_dialogue_preserves_its_tail_and_does_not_block_later_memories(
    db_session, memory_store
):
    room, player, actor = await _create_memory_room(db_session, 710)
    text = "字" * 1990 + "这是原话末尾的重要内容"
    text = text[-2000:]
    db_session.add_all(
        [
            dialogue(room, player, text=text, speaker=actor, listeners=("npc",)),
            dialogue(room, player, text="后续正常回答", offset=1),
        ]
    )
    await db_session.commit()
    for _ in range(2):
        context = await read(memory_store, room, player, actor)
        assert any(text in entry.content for entry in context.entries)
        assert any("后续正常回答" in entry.content for entry in context.entries)
    stored = list(
        (
            await db_session.scalars(
                select(MemoryEntryRecord).where(MemoryEntryRecord.room_id == room.id)
            )
        ).all()
    )
    assert any(text in row.content for row in stored)


async def test_scene_memory_requires_a_frozen_audience(db_session, memory_store):
    room, player, actor = await _create_memory_room(db_session, 711)
    other = Player(id=str(uuid.uuid4()), room_id=room.id, nickname="未在场玩家")
    missing = dialogue(room, player, text="没有冻结受众", visibility="scene_scoped")
    allowed = dialogue(room, player, text="只有在场玩家能读", visibility="scene_scoped", offset=1)
    db_session.add_all(
        [other, missing, allowed, EventAudience(event_id=allowed.id, player_id=player.id)]
    )
    await db_session.commit()
    owner = await read(memory_store, room, player, actor)
    outsider = await read(memory_store, room, other, actor)
    assert [entry.content for entry in owner.entries] == ["只有在场玩家能读"]
    assert not outsider.entries


async def test_projection_refresh_failure_keeps_committed_memories(
    db_session, memory_store, monkeypatch
):
    room, player, actor = await _create_memory_room(db_session, 712)
    db_session.add(dialogue(room, player, text="已经保存的经历"))
    await db_session.commit()
    await memory_store.project_room_events(room.id)
    monkeypatch.setattr(
        memory_store, "project_room_events", AsyncMock(side_effect=ValueError("bad source"))
    )
    context = await read(memory_store, room, player, actor)
    assert [entry.content for entry in context.entries] == ["已经保存的经历"]
