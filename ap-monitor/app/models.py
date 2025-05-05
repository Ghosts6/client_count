from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base

class Building(Base):
    __tablename__ = "buildings"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    floors = relationship("Floor", back_populates="building")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Floor(Base):
    __tablename__ = "floors"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False)
    building = relationship("Building", back_populates="floors")
    access_points = relationship("AccessPoint", back_populates="floor")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Room(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    floor_id = Column(Integer, ForeignKey("floors.id"), nullable=False)
    floor = relationship("Floor")
    access_points = relationship("AccessPoint", back_populates="room")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class AccessPoint(Base):
    __tablename__ = "access_points"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    mac_address = Column(String, nullable=False, unique=True)
    ip_address = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    floor_id = Column(Integer, ForeignKey("floors.id"), nullable=True)
    floor = relationship("Floor", back_populates="access_points")
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=True)
    room = relationship("Room", back_populates="access_points")
    clients = Column(Integer, nullable=False, default=0)
    client_counts = relationship("ClientCount", back_populates="access_point")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ClientCount(Base):
    __tablename__ = "client_counts"
    id = Column(Integer, primary_key=True, index=True)
    ap_id = Column(Integer, ForeignKey("access_points.id"), nullable=False)
    access_point = relationship("AccessPoint", back_populates="client_counts")
    radio_id = Column(Integer, nullable=False)
    client_count = Column(Integer, nullable=False, default=0)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Radio(Base):
    __tablename__ = "radios"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())