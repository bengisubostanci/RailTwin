"""Demo modu: canlı ikiz üzerinde baştan sona bir arıza hikâyesi oynatır.

Seçilen trene rulman arızası enjekte edilir, modelin önce bozulmayı sonra kritik
durumu fark etmesi beklenir, bakım ekibi gönderilir ve tren toparlanana kadar
izlenir. Her adım canlı görüntüyle panolara yayılır. Simülatör arızayı backend'e
bildirmez; demo da modelin kararını beklemek zorundadır, hiçbir sonucu taklit etmez.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from .twin import ACTIONS, METRIC_LABELS, LiveTwin

log = logging.getLogger(__name__)

FAULT_KIND = "bearing_overheat"
STEP_TIMEOUT_S = 300.0
POLL_S = 1.0
HEALTHY = 75.0
CRITICAL = 45.0
READ_PAUSE_S = 6.0  # izleyicinin alarmı okuyabilmesi için bakım öncesi bekleme

STEPS = [
    ("select", "Tren seçiliyor"),
    ("inject", "Arıza başlıyor"),
    ("detect", "Model bozulmayı fark ediyor"),
    ("alarm", "Kritik alarm ve teşhis"),
    ("dispatch", "Bakım ekibi gönderiliyor"),
    ("recover", "Tren normale dönüyor"),
]


class DemoError(Exception):
    """Demo başlatılamadığında, kullanıcıya gösterilecek mesajla."""


class DemoRunner:
    def __init__(
        self,
        twin: LiveTwin,
        control: Callable[[dict], Awaitable[None]],
        dispatch: Callable[[str], Awaitable[dict]],
    ):
        self.twin = twin
        self.control = control
        self.dispatch = dispatch
        self._task: asyncio.Task | None = None
        self._fault_active = False
        self._reset(None)

    # ---------------------------------------------------------------- durum
    def _reset(self, asset_id: str | None) -> None:
        self.asset_id = asset_id
        self.state = "idle"  # idle | running | done | failed | stopped
        self.message: str | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.steps = [
            {"key": key, "title": title, "status": "pending", "detail": None, "at_s": None}
            for key, title in STEPS
        ]

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def view(self) -> dict:
        end = self.finished_at or time.monotonic()
        return {
            "state": self.state,
            "asset_id": self.asset_id,
            "asset_name": self.twin.names.get(self.asset_id, self.asset_id) if self.asset_id else None,
            "message": self.message,
            "elapsed_s": round(end - self.started_at, 1) if self.started_at else None,
            "steps": self.steps,
        }

    def _step(self, key: str, status: str, detail: str | None = None) -> None:
        for step in self.steps:
            if step["key"] == key:
                step["status"] = status
                if detail is not None:
                    step["detail"] = detail
                if status in ("active", "done", "failed") and self.started_at is not None:
                    step["at_s"] = round(time.monotonic() - self.started_at, 1)

    # ------------------------------------------------------------- kontrol
    def pick_train(self) -> str:
        """Seferde olan, ısınmasını bitirmiş ve sağlıklı bir tren seçer."""
        candidates = []
        for asset_id, kind in self.twin.types.items():
            if kind != "train" or asset_id not in self.twin.health:
                continue
            view = self.twin.asset_view(asset_id)
            if view["warming"] or not view["online"] or view["health"] < 90:
                continue
            if any(key[0] == asset_id for key in self.twin.active_alerts):
                continue
            running = view["extra"].get("status") == "running"
            candidates.append((not running, -view["health"], asset_id))
        if not candidates:
            raise DemoError("Demo için uygun tren yok: trenler hâlâ ısınıyor veya hepsinde aktif alarm var.")
        return min(candidates)[2]

    async def start(self, asset_id: str | None = None) -> dict:
        if self.running:
            raise DemoError("Bir demo zaten çalışıyor.")
        if not self.twin.models.ready or not self.twin.mqtt_connected:
            raise DemoError("Demo için modellerin hazır ve saha verisinin geliyor olması gerekir.")
        if asset_id is None:
            asset_id = self.pick_train()
        elif self.twin.types.get(asset_id) != "train":
            raise DemoError(f"{asset_id} bir tren değil; demo yalnızca trenlerle çalışır.")
        self._reset(asset_id)
        self.state = "running"
        self.started_at = time.monotonic()
        self._task = asyncio.create_task(self._run(asset_id))
        return self.view()

    async def stop(self) -> dict:
        if self.running:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        return self.view()

    async def close(self) -> None:
        await self.stop()

    # ---------------------------------------------------------------- akış
    def _health(self, asset_id: str):
        return self.twin.health.get(asset_id)

    async def _wait(self, condition: Callable[[], bool], what: str) -> None:
        deadline = time.monotonic() + STEP_TIMEOUT_S
        while not condition():
            if time.monotonic() > deadline:
                raise TimeoutError(f"{what} {STEP_TIMEOUT_S / 60:.0f} dakika içinde gerçekleşmedi.")
            await asyncio.sleep(POLL_S)

    async def _run(self, asset_id: str) -> None:
        name = self.twin.names.get(asset_id, asset_id)
        current = "select"
        try:
            self._step("select", "done", f"{name} seçildi: seferde, sağlık %{self._health(asset_id).health:.0f}.")

            current = "inject"
            self._step("inject", "active")
            await self.control({"action": "inject_fault", "asset_id": asset_id, "kind": FAULT_KIND})
            self._fault_active = True
            self._step("inject", "done",
                       "Aks rulmanında aşınma başladı ve sıcaklık yavaşça yükseliyor. Simülatör bunu backend'e "
                       "bildirmez; arızayı model kendi bulmak zorunda.")

            current = "detect"
            self._step("detect", "active", "Model, rulman sıcaklığını hız, yük ve hava koşullarından beklenen "
                                           "değerle karşılaştırıyor…")
            await self._wait(lambda: self._health(asset_id).health < HEALTHY, "Bozulma tespiti")
            h = self._health(asset_id)
            z = h.z.get("bearing_temp_c")
            sapma = f", rulman sıcaklığı beklenenden {z:+.1f}σ sapmış".replace(".", ",") if z is not None else ""
            self._step("detect", "done", f"Sağlık puanı %{h.health:.0f} seviyesine düştü{sapma}. Tren “bozulmuş” olarak işaretlendi.")

            current = "alarm"
            self._step("alarm", "active", "Arıza ilerliyor, sağlık puanı düşmeye devam ediyor…")
            await self._wait(lambda: (asset_id, "ml") in self.twin.active_alerts
                             and self.twin.active_alerts[(asset_id, "ml")]["severity"] == "critical",
                             "Kritik alarm")
            h = self._health(asset_id)
            cause = METRIC_LABELS.get(h.cause or "", "birden fazla sensör")
            action = ACTIONS.get(h.cause or "", "Genel depo muayenesi")
            self._step("alarm", "done", f"Kritik alarm açıldı. Olası sebep: {cause}. Önerilen işlem: {action}.")

            current = "dispatch"
            self._step("dispatch", "active", "Bakım kuyruğunda en üstte; ekip hazırlanıyor…")
            await asyncio.sleep(READ_PAUSE_S)
            order = await self.dispatch(asset_id)
            self._fault_active = False
            self._step("dispatch", "done", f"İş emri #{order['id']} açıldı: {order['action']}.")

            current = "recover"
            self._step("recover", "active", "Rulman değiştirildi; model sensörlerin normale dönmesini izliyor…")
            await self._wait(lambda: self._health(asset_id).health >= HEALTHY, "Toparlanma")
            self.finished_at = time.monotonic()
            total = self.finished_at - self.started_at
            self._step("recover", "done", f"Sağlık puanı yeniden %{self._health(asset_id).health:.0f}. "
                                          f"{name} tekrar sağlıklı.")
            self.state = "done"
            self.message = f"Demo tamamlandı: {int(total // 60)} dk {int(total % 60)} sn."
        except asyncio.CancelledError:
            self._step(current, "failed", "Demo durduruldu.")
            self.state = "stopped"
            self.message = "Demo durduruldu."
            raise
        except Exception as exc:  # noqa: BLE001 - her hata demoyu sonlandırır, panelde gösterilir
            log.exception("Demo başarısız oldu")
            self._step(current, "failed", str(exc))
            self.state = "failed"
            self.message = f"Demo yarıda kaldı: {exc}"
        finally:
            self.finished_at = self.finished_at or time.monotonic()
            if self._fault_active:
                # Enjekte ettiğimiz arızayı hatta bırakmayalım.
                with contextlib.suppress(Exception):
                    await self.control({"action": "repair", "asset_id": asset_id})
                self._fault_active = False
