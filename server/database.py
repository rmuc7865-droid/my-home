from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .settings import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    device: Mapped[str] = mapped_column(String(128), index=True)
    system: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    measurements_json: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    alerts: Mapped[list["Alert"]] = relationship(back_populates="measurement")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    measurement_id: Mapped[int] = mapped_column(ForeignKey("measurements.id"), index=True)
    rule_name: Mapped[str] = mapped_column(String(128), index=True)
    system: Mapped[str] = mapped_column(String(64), index=True)
    field: Mapped[str] = mapped_column(String(128))
    actual_value: Mapped[str] = mapped_column(Text)
    expected_operator: Mapped[str] = mapped_column(String(16))
    expected_value: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    measurement: Mapped[Measurement] = relationship(back_populates="alerts")

class SimulationTrade(Base):
    __tablename__ = "simulation_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    ticker_name: Mapped[str] = mapped_column(String(256), default="")
    buy_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    buy_price: Mapped[float] = mapped_column(Float)
    sell_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    sell_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    relative_difference: Mapped[float | None] = mapped_column(Float, nullable=True)
    absolute_difference: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_telegram_sent: Mapped[bool] = mapped_column(Boolean, default=True)
    sell_telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
