"""Saha simülatörü: hattı gerçek zamanlı çalıştırır ve sensör verilerini MQTT üzerinden yayınlar.

Gerçek bir hattın hat kenarı ve araç üstü IoT ağ geçitlerinin yerini tutar. Backend
yalnızca gerçek bir kurulumda göreceği şeyi görür: sensör okumalarını; gerçek
arıza durumunu asla görmez.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import aiomqtt

from twin_core import TwinEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s simulator: %(message)s")
log = logging.getLogger("simulator")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TIME_SCALE = float(os.getenv("TIME_SCALE", "5"))
TICK_S = float(os.getenv("TICK_S", "1.0"))
SPONTANEOUS_FAULTS = os.getenv("SPONTANEOUS_FAULTS", "true").lower() == "true"
FAULT_INTERVAL_S = float(os.getenv("FAULT_INTERVAL_S", "300"))
SEED = int(os.environ["SEED"]) if os.getenv("SEED") else None

engine = TwinEngine(
    seed=SEED,
    time_scale=TIME_SCALE,
    spontaneous_faults=SPONTANEOUS_FAULTS,
    fault_interval_s=FAULT_INTERVAL_S,
)


async def publish_loop(client: aiomqtt.Client) -> None:
    topology = {**engine.topo.to_dict(), "time_scale": TIME_SCALE}
    await client.publish("railtwin/topology", json.dumps(topology), qos=1, retain=True)
    last = time.monotonic()
    known_faults: dict = {}
    while True:
        await asyncio.sleep(max(0.0, TICK_S - (time.monotonic() - last)))
        now = time.monotonic()
        readings = engine.step(now - last)
        last = now
        for reading in readings:
            topic = f"railtwin/telemetry/{reading['type']}/{reading['asset_id']}"
            await client.publish(topic, json.dumps(reading))
        faults = engine.active_faults()
        for asset_id in faults.keys() - known_faults.keys():
            log.info("%s üzerinde bozulma başladı: %s", asset_id, faults[asset_id])
        known_faults = faults


async def control_loop(client: aiomqtt.Client) -> None:
    await client.subscribe("railtwin/control", qos=1)
    async for message in client.messages:
        try:
            cmd = json.loads(message.payload)
            action = cmd.get("action")
            if action == "inject_fault":
                kind = engine.inject_fault(cmd["asset_id"], cmd.get("kind"))
                log.info("%s üzerine arıza enjekte edildi: %s", cmd["asset_id"], kind)
                event = {"event": "fault_injected", "asset_id": cmd["asset_id"]}
            elif action == "repair":
                repaired = engine.repair(cmd["asset_id"])
                log.info("%s üzerinde bakım tamamlandı (giderilen: %s)", cmd["asset_id"], repaired or "hiçbiri")
                event = {"event": "maintenance_completed", "asset_id": cmd["asset_id"]}
            elif action == "set_env":
                engine.set_env(cmd.get("ambient_c"), cmd.get("load_factor"))
                log.info("Ortam koşulları ayarlandı: %s", engine.env)
                event = {"event": "environment_changed", "ambient_c": engine.env.ambient_c,
                         "load_factor": engine.env.load_factor}
            else:
                log.warning("Bilinmeyen kontrol komutu: %s", cmd)
                continue
            await client.publish("railtwin/events", json.dumps(event))
        except (KeyError, ValueError) as exc:
            log.warning("Kontrol mesajı reddedildi %s: %s", message.payload, exc)


async def main() -> None:
    log.info("Hat simülasyonu başlatılıyor: gerçek zamanın x%s katı, kendiliğinden arızalar=%s", TIME_SCALE, SPONTANEOUS_FAULTS)
    while True:
        try:
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT, identifier="railtwin-simulator") as client:
                log.info("MQTT broker'ına bağlanıldı %s:%s", MQTT_HOST, MQTT_PORT)
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(publish_loop(client))
                    tg.create_task(control_loop(client))
        except* aiomqtt.MqttError as group:
            log.warning("MQTT bağlantısı koptu (%s); 3 s içinde yeniden bağlanılıyor", group.exceptions[0])
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
