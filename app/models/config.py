from pydantic import BaseModel, Field


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


class RetentionConfig(BaseModel):
    enabled: bool = False
    days: int = 30
    check_interval_hours: int = 24


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
