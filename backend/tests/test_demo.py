"""Demo modunun adım akışı, sahte bir canlı ikiz üzerinde."""

import asyncio
from dataclasses import dataclass, field

import pytest

from app import demo as demo_mod
from app.demo import DemoError, DemoRunner


@dataclass
class FakeHealth:
    health: float = 98.0
    cause: str | None = None
    z: dict = field(default_factory=dict)


class FakeModels:
    ready = True


class FakeTwin:
    """Arıza varken sağlığı düşüren, onarımdan sonra toparlayan basit bir ikiz."""

    def __init__(self, trains=("T-101", "T-102")):
        self.models = FakeModels()
        self.mqtt_connected = True
        self.types = {t: "train" for t in trains} | {"SEG-01": "segment"}
        self.names = {t: f"Tren {t[2:]}" for t in trains}
        self.health = {a: FakeHealth() for a in self.types}
        self.active_alerts: dict = {}
        self.faulted: set[str] = set()
        self.commands: list[dict] = []

    def asset_view(self, asset_id):
        return {"health": self.health[asset_id].health, "warming": False, "online": True,
                "extra": {"status": "running"}}

    async def control(self, cmd):
        self.commands.append(cmd)
        if cmd["action"] == "inject_fault":
            self.faulted.add(cmd["asset_id"])
        elif cmd["action"] == "repair":
            self.faulted.discard(cmd["asset_id"])

    async def dispatch(self, asset_id):
        await self.control({"action": "repair", "asset_id": asset_id})
        return {"id": 7, "action": "Aks rulmanını kontrol et ve değiştir"}

    async def tick(self):
        while True:
            for asset_id, h in self.health.items():
                if asset_id in self.faulted:
                    h.health = max(0.0, h.health - 8)
                    h.cause, h.z = "bearing_temp_c", {"bearing_temp_c": 9.5}
                else:
                    h.health = min(98.0, h.health + 15)
                if h.health < 45:
                    self.active_alerts[(asset_id, "ml")] = {"severity": "critical"}
                else:
                    self.active_alerts.pop((asset_id, "ml"), None)
            await asyncio.sleep(0.005)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(demo_mod, "POLL_S", 0.005)
    monkeypatch.setattr(demo_mod, "READ_PAUSE_S", 0.0)
    monkeypatch.setattr(demo_mod, "STEP_TIMEOUT_S", 5.0)


async def _run_demo(twin, runner, until):
    ticker = asyncio.create_task(twin.tick())
    try:
        await runner.start()
        for _ in range(2000):
            if until(runner):
                break
            await asyncio.sleep(0.005)
    finally:
        ticker.cancel()


def test_demo_runs_all_steps_and_leaves_line_clean():
    twin = FakeTwin()
    runner = DemoRunner(twin, twin.control, twin.dispatch)

    async def scenario():
        await _run_demo(twin, runner, lambda r: not r.running)

    asyncio.run(scenario())
    view = runner.view()
    assert view["state"] == "done", view
    assert [s["status"] for s in view["steps"]] == ["done"] * 6
    assert "aks rulmanı sıcaklığı" in view["steps"][3]["detail"]
    assert "#7" in view["steps"][4]["detail"]
    assert not twin.faulted
    assert [c["action"] for c in twin.commands] == ["inject_fault", "repair"]


def test_stopping_mid_demo_repairs_the_injected_fault():
    twin = FakeTwin()
    runner = DemoRunner(twin, twin.control, twin.dispatch)

    async def scenario():
        await _run_demo(twin, runner, lambda r: r.steps[2]["status"] == "active")
        await runner.stop()

    asyncio.run(scenario())
    assert runner.view()["state"] == "stopped"
    assert not twin.faulted, "durdurulan demo arızayı hatta bıraktı"


def test_demo_refuses_when_no_train_is_healthy():
    twin = FakeTwin()
    for h in twin.health.values():
        h.health = 50.0
    runner = DemoRunner(twin, twin.control, twin.dispatch)
    with pytest.raises(DemoError):
        asyncio.run(runner.start())
