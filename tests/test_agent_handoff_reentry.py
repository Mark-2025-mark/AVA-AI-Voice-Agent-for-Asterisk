"""AVA→AVA agent handoff when dialplan continue re-enters Stasis on the same channel_id.

Motostore 7101 (recepcionista) → blind_transfer → 7102 (ventas) keeps the Asterisk
channel id. Without handoff, StasisStart hits "Caller already in progress" and the
destination agent never starts.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine


def _make_engine() -> Engine:
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()

    async def _send_command(method, path, params=None, tolerate_statuses=None):
        variable = (params or {}).get("variable")
        if variable == "AI_AGENT":
            return {"value": "ventas_motostore"}
        return {"value": ""}

    engine.ari_client = SimpleNamespace(
        create_bridge=AsyncMock(return_value="bridge-handoff"),
        add_channel_to_bridge=AsyncMock(return_value=True),
        destroy_bridge=AsyncMock(return_value=True),
        hangup_channel=AsyncMock(return_value=True),
        send_command=AsyncMock(side_effect=_send_command),
        answer_channel=AsyncMock(return_value=True),
    )
    engine.bridges = {}
    engine._call_providers = {}
    engine._provider_start_tasks = {}
    engine.providers = {"deepgram": object()}
    engine.provider_kinds = {"deepgram": "deepgram"}
    engine.config = SimpleNamespace(
        default_provider="deepgram",
        audio_transport="externalmedia",
        providers={"deepgram": {}},
    )
    engine.vad_manager = None
    engine.transport_orchestrator = SimpleNamespace(
        agent_store=SimpleNamespace(default_slug=lambda: None),
        get_context_config=MagicMock(return_value=None),
        yaml_context_shadowed_by_agent_db=MagicMock(return_value=False),
    )
    engine.call_media_lifecycle = None
    engine.media_transport_runtime = None
    engine.pipeline_orchestrator = SimpleNamespace(enabled=False)
    engine._tool_generation = None
    engine._transfer_stasis_hop_tasks = {}
    engine._transfer_stasis_hop_timeout_sec = 5.0
    engine._seen_aux_channels = set()
    engine._outbound_awaiting_amd_channel_ids = set()

    async def _save(session, new=False, require_current=False):
        await engine.session_store.upsert_call(session)
        return True

    engine._save_session = _save  # type: ignore[method-assign]
    engine._resolve_session_tool_runtime = MagicMock()
    engine._stop_call_provider_instance = AsyncMock()
    engine._stop_connection_audio = AsyncMock()
    engine._hydrate_transport_from_dialplan = AsyncMock()
    engine._detect_caller_codec = AsyncMock()
    engine._resolve_audio_profile = AsyncMock()
    engine._assign_session_provider = Engine._assign_session_provider.__get__(engine, Engine)
    engine._should_use_local_vad = MagicMock(return_value=False)
    engine._assign_pipeline_to_session = AsyncMock(return_value=None)
    engine._ensure_pipeline_runner = AsyncMock()
    engine._handle_provider_start_failure = AsyncMock()
    engine._handle_pipeline_resolution_failure = AsyncMock()
    engine._start_external_media_channel = AsyncMock(return_value="ext-media-1")
    engine._ensure_provider_session_started = AsyncMock()
    engine._cleanup_call = AsyncMock()
    engine._suspend_call_resources_for_stasis_hop = AsyncMock(
        side_effect=lambda session: Engine._suspend_call_resources_for_stasis_hop(
            engine, session
        )
    )
    return engine


@pytest.mark.asyncio
async def test_existing_session_without_transfer_still_guards():
    engine = _make_engine()
    session = CallSession(
        call_id="chan-1",
        caller_channel_id="chan-1",
        provider_name="deepgram",
        context_name="recepcionista_motostore",
    )
    await engine.session_store.upsert_call(session)

    await Engine._handle_caller_stasis_start_hybrid(
        engine, "chan-1", {"caller": {"name": "A", "number": "1"}}
    )

    engine._resolve_audio_profile.assert_not_awaited()
    engine.ari_client.create_bridge.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_active_reentry_rebinds_agent():
    engine = _make_engine()

    async def _resolve(session, channel_id):
        session.context_name = "ventas_motostore"
        session.routing_method = "ai_agent"
        session.provider_name = "deepgram"
        await engine.session_store.upsert_call(session)

    engine._resolve_audio_profile = AsyncMock(side_effect=_resolve)

    session = CallSession(
        call_id="chan-1",
        caller_channel_id="chan-1",
        provider_name="deepgram",
        context_name="recepcionista_motostore",
        transfer_active=True,
        transfer_target="Ventas Motostore (ext 7102)",
        bridge_id="bridge-old",
        external_media_id="ext-old",
        provider_session_active=True,
    )
    await engine.session_store.upsert_call(session)
    engine._call_providers["chan-1"] = object()

    await Engine._handle_caller_stasis_start_hybrid(
        engine,
        "chan-1",
        {
            "caller": {"name": "Caller", "number": "629515959"},
            "dialplan": {"context": "from-fspbx", "exten": "7102"},
        },
    )

    updated = await engine.session_store.get_by_call_id("chan-1")
    assert updated is not None
    assert updated.context_name == "ventas_motostore"
    assert updated.routing_method == "ai_agent"
    assert updated.transfer_active is False
    assert updated.agent_handoff_in_progress is False
    assert updated.agent_handoff_pending is False
    assert updated.bridge_id == "bridge-handoff"
    assert updated.transfer_destination == "Ventas Motostore (ext 7102)"
    engine._resolve_audio_profile.assert_awaited()
    engine._ensure_provider_session_started.assert_awaited()
    engine.ari_client.create_bridge.assert_awaited()


@pytest.mark.asyncio
async def test_stasis_end_during_transfer_suspends_instead_of_full_cleanup():
    engine = _make_engine()
    engine._transfer_stasis_hop_timeout_sec = 0.05
    session = CallSession(
        call_id="chan-1",
        caller_channel_id="chan-1",
        provider_name="deepgram",
        context_name="recepcionista_motostore",
        transfer_active=True,
        transfer_target="Ventas",
        bridge_id="bridge-1",
        external_media_id="ext-1",
        provider_session_active=True,
    )
    await engine.session_store.upsert_call(session)
    engine._call_providers["chan-1"] = object()
    # Avoid awaiting the real suspend body for this unit — verify hop scheduling.
    engine._suspend_call_resources_for_stasis_hop = AsyncMock()

    await Engine._handle_stasis_end(
        engine, {"channel": {"id": "chan-1"}}
    )

    engine._cleanup_call.assert_not_awaited()
    engine._suspend_call_resources_for_stasis_hop.assert_awaited()
    still = await engine.session_store.get_by_call_id("chan-1")
    assert still is not None
    assert still.agent_handoff_pending is True
    assert "chan-1" in engine._transfer_stasis_hop_tasks

    await engine._transfer_stasis_hop_tasks["chan-1"]
    engine._cleanup_call.assert_awaited()


@pytest.mark.asyncio
async def test_stasis_end_ignored_while_handoff_in_progress():
    engine = _make_engine()
    session = CallSession(
        call_id="chan-1",
        caller_channel_id="chan-1",
        provider_name="deepgram",
        agent_handoff_in_progress=True,
        transfer_active=True,
    )
    await engine.session_store.upsert_call(session)

    await Engine._handle_stasis_end(engine, {"channel": {"id": "chan-1"}})

    engine._cleanup_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_aux_cleanup_skipped_during_stasis_hop():
    engine = _make_engine()
    session = CallSession(
        call_id="chan-1",
        caller_channel_id="chan-1",
        provider_name="deepgram",
        agent_handoff_pending=True,
        transfer_active=True,
        external_media_id="ext-1",
    )
    await engine.session_store.upsert_call(session)

    # Restore real cleanup entry long enough to hit the hop guard.
    real_cleanup = Engine._cleanup_call
    engine._cleanup_call = real_cleanup.__get__(engine, Engine)

    await Engine._cleanup_call(engine, "ext-1")

    still = await engine.session_store.get_by_call_id("chan-1")
    assert still is not None
    assert still.agent_handoff_pending is True


@pytest.mark.asyncio
async def test_drop_channel_aliases_prevents_aux_lookup():
    store = SessionStore()
    session = CallSession(
        call_id="chan-1",
        caller_channel_id="chan-1",
        provider_name="deepgram",
        external_media_id="ext-1",
    )
    await store.upsert_call(session)
    assert await store.get_by_channel_id("ext-1") is session

    await store.drop_channel_aliases(session, ["ext-1"])
    assert await store.get_by_channel_id("ext-1") is None
    assert await store.get_by_call_id("chan-1") is session
