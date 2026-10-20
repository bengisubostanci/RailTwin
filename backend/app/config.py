import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql://railtwin:railtwin@localhost:5432/railtwin")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    mqtt_host: str = os.getenv("MQTT_HOST", "localhost")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    telemetry_retention: str = os.getenv("TELEMETRY_RETENTION", "2 days")
    baseline_steps: int = int(os.getenv("BASELINE_STEPS", "36000"))
    model_cache_dir: str | None = os.getenv("MODEL_CACHE_DIR") or None
    cors_origins: tuple[str, ...] = tuple(
        o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
    )


settings = Settings()

TOPIC_ROOT = "railtwin"
TOPIC_TELEMETRY = f"{TOPIC_ROOT}/telemetry/#"
TOPIC_TOPOLOGY = f"{TOPIC_ROOT}/topology"
TOPIC_CONTROL = f"{TOPIC_ROOT}/control"
TOPIC_EVENTS = f"{TOPIC_ROOT}/events"
