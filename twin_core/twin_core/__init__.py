"""RailTwin dijital ikizinin ortak çekirdeği: hat topolojisi ve fizik motoru.

Simülatör servisi motoru gerçek zamanlı çalıştırır ve sensör okumalarını MQTT
üzerinden yayınlar. Backend aynı motoru arayüzsüz ve arızasız çalıştırarak canlı
veriyi puanlamadan önce "sağlıklı" davranışın neye benzediğini öğrenir.
"""

from .engine import FAULT_KINDS, TwinEngine
from .topology import Topology, default_topology

__all__ = ["FAULT_KINDS", "TwinEngine", "Topology", "default_topology"]
