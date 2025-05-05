from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base

class Building(Base):
    __tablename__ = "buildings"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    floors = relationship("Floor", back_populates="building")

class Floor(Base):
    __tablename__ = "floors"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False)
    building = relationship("Building", back_populates="floors")
    access_points = relationship("AccessPoint", back_populates="floor")

class AccessPoint(Base):
    __tablename__ = "access_points"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    mac_address = Column(String, nullable=False, unique=True)
    ip_address = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    floor_id = Column(Integer, ForeignKey("floors.id"), nullable=False)
    floor = relationship("Floor", back_populates="access_points")
    clients = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ClientCount(Base):
    __tablename__ = "client_counts"
    id = Column(Integer, primary_key=True, index=True)
    ap_id = Column(Integer, ForeignKey("access_points.id"), nullable=False)
    radio_id = Column(Integer, nullable=False)
    client_count = Column(Integer, nullable=False, default=0)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)