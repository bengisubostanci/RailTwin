"""MQTT köprüsü: telemetri ve topoloji gelir, kontrol komutları gider."""

from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

from .config import TOPIC_CONTROL, TOPIC_EVENTS, TOPIC_TELEMETRY, TOPIC_TOPOLOGY, settings
from .db import Database, utcnow
from .twin import LiveTwin

log = logging.getLogger(__name__)


class MqttBridge:
    def __init__(self, twin: LiveTwin, db: Database):
        self.twin = twin
        self.db = db
        self._client: aiomqtt.Client | None = None

    async def run(self) -> None:
        while True:
            try:
                async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port, identifier="railtwin-backend") as client:
                    self._client = client
                    self.twin.mqtt_connected = True
                    log.info("MQTT aracısına bağlanıldı %s:%s", settings.mqtt_host, settings.mqtt_port)
                    # Önce topoloji: kalıcı (retained) olduğu için her telemetriden önce gelir.
                    for topic in (TOPIC_TOPOLOGY, TOPIC_TELEMETRY, TOPIC_EVENTS):
                        await client.subscribe(topic)
                    async for message in client.messages:
                        await self._handle(message)
            except aiomqtt.MqttError as exc:
                log.warning("MQTT bağlantısı koptu (%s); 3 s içinde yeniden bağlanılıyor", exc)
            finally:
                self._client = None
                self.twin.mqtt_connected = False
            await asyncio.sleep(3)

    async def _handle(self, message: aiomqtt.Message) -> None:
        try:
            payload = json.loads(message.payload)
        except (TypeError, ValueError):
            log.warning("%s üzerindeki JSON olmayan mesaj yok sayılıyor", message.topic)
            return
        topic = message.topic
        if topic.matches(TOPIC_TELEMETRY):
            self.twin.on_reading(payload)
            self.db.buffer_reading(utcnow(), payload)
        elif topic.matches(TOPIC_TOPOLOGY):
            await self.twin.on_topology(payload)
        elif topic.matches(TOPIC_EVENTS):
            log.info("Simülatör olayı: %s", payload)

    async def send_control(self, command: dict) -> None:
        if self._client is None:
            raise RuntimeError("MQTT aracısına bağlı değil")
        await self._client.publish(TOPIC_CONTROL, json.dumps(command), qos=1)
