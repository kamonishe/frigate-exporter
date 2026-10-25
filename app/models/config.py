from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class FrigateConfig(BaseModel):
    url: str
    username: str
    password: str
    verify_ssl: bool = True
    timeout: int = 30


class MQTTConfig(BaseModel):
    host: str
    port: int
    username: str
    password: str
    topic: str


class ExportConfig(BaseModel):
    output: str
    workers: int
    pre_capture: int
    post_capture: int
    export_timeout: int = Field(default=900, ge=1)
    max_attempts: int = Field(default=5, ge=1)


class RetentionConfig(BaseModel):
    enabled: bool = False
    days: int | None = Field(default=None, ge=1)
    hours: int | None = Field(default=None, ge=1)
    check_interval_hours: int = Field(default=24, ge=1)

    @model_validator(mode="after")
    def validate_window(self) -> RetentionConfig:
        if self.days is not None and self.hours is not None:
            raise ValueError("retention.days and retention.hours cannot both be set")
        if self.days is None and self.hours is None:
            self.days = 30
        return self

    @property
    def retention_seconds(self) -> int:
        if self.hours is not None:
            return self.hours * 60 * 60
        assert self.days is not None
        return self.days * 24 * 60 * 60

    @property
    def retention_label(self) -> str:
        if self.hours is not None:
            unit = "hour" if self.hours == 1 else "hours"
            return f"{self.hours} {unit}"
        assert self.days is not None
        unit = "day" if self.days == 1 else "days"
        return f"{self.days} {unit}"


class FilterConfig(BaseModel):
    cameras: list[str]
    labels: list[str]
    severity: list[str]


class LoggingConfig(BaseModel):
    level: str


class DashboardConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 5050
    username: str | None = None
    password: str | None = None


class ReconciliationConfig(BaseModel):
    enabled: bool = True
    interval_minutes: int = Field(10, ge=1)
    lookback_days: int = Field(2, ge=1)


class Config(BaseModel):
    frigate: FrigateConfig
    mqtt: MQTTConfig
    export: ExportConfig
    retention: RetentionConfig
    filters: FilterConfig
    logging: LoggingConfig
    dashboard: DashboardConfig = Field(
        default_factory=DashboardConfig
    )
    reconciliation: ReconciliationConfig = Field(
        default_factory=ReconciliationConfig
    )
