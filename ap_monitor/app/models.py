from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, BigInteger, Numeric
from sqlalchemy.dialects.postgresql import MACADDR, INET
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ap_monitor.app.db import Base
import os

if os.getenv("TESTING", "false").lower() == "true":
    # Use String for SQLite testing
    MACADDR_TYPE = String(17)  # MAC addresses 
    INET_TYPE = String(45)     # IPv6 addresses 
else:
    MACADDR_TYPE = MACADDR
    INET_TYPE = INET

# wireless_count DB models
class Campus(Base):
    __tablename__ = "campuses"
    campus_id = Column(Integer, primary_key=True, autoincrement=True)
    campus_name = Column(String(100), nullable=False, unique=True)
    buildings = relationship("Building", back_populates="campus", cascade="all, delete-orphan")

class Building(Base):
    __tablename__ = "buildings"
    building_id = Column(Integer, primary_key=True, autoincrement=True)
    building_name = Column(String(100), nullable=False)
    campus_id = Column(Integer, ForeignKey("campuses.campus_id", ondelete="CASCADE"), nullable=False)
    latitude = Column(Numeric(15, 10), nullable=False)
    longitude = Column(Numeric(15, 10), nullable=False)
    campus = relationship("Campus", back_populates="buildings")
    client_counts = relationship("ClientCount", back_populates="building", cascade="all, delete-orphan")

    __table_args__ = (
        {'extend_existing': True},
    )

class ClientCount(Base):
    __tablename__ = "client_counts"
    count_id = Column(Integer, primary_key=True, autoincrement=True)
    building_id = Column(Integer, ForeignKey("buildings.building_id", ondelete="CASCADE"), nullable=False)
    client_count = Column(Integer, nullable=False)
    time_inserted = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    building = relationship("Building", back_populates="client_counts")

# apclientcount DB models
class ApBuilding(Base):
    __tablename__ = "ap_buildings"
    buildingid = Column(Integer, primary_key=True, autoincrement=True)
    buildingname = Column(String(255), nullable=False)
    floors = relationship("Floor", back_populates="building", cascade="all, delete-orphan")

class Floor(Base):
    __tablename__ = "floors"
    floorid = Column(Integer, primary_key=True, autoincrement=True)
    buildingid = Column(Integer, ForeignKey("ap_buildings.buildingid", ondelete="CASCADE"))
    floorname = Column(String(50), nullable=False)
    building = relationship("ApBuilding", back_populates="floors")
    rooms = relationship("Room", back_populates="floor", cascade="all, delete-orphan")
    accesspoints = relationship("AccessPoint", back_populates="floor", cascade="all, delete-orphan")

class Room(Base):
    __tablename__ = "rooms"
    roomid = Column(Integer, primary_key=True, autoincrement=True)
    floorid = Column(Integer, ForeignKey("floors.floorid", ondelete="CASCADE"))
    roomname = Column(String(100), nullable=False)
    floor = relationship("Floor", back_populates="rooms")
    accesspoints = relationship("AccessPoint", back_populates="room", cascade="all, delete-orphan")

class AccessPoint(Base):
    __tablename__ = "accesspoints"
    apid = Column(Integer, primary_key=True, autoincrement=True)
    buildingid = Column(Integer, ForeignKey("ap_buildings.buildingid", ondelete="CASCADE"))
    floorid = Column(Integer, ForeignKey("floors.floorid", ondelete="CASCADE"))
    roomid = Column(Integer, ForeignKey("rooms.roomid", ondelete="CASCADE"))
    apname = Column(String(40), nullable=False)
    macaddress = Column(MACADDR_TYPE)
    ipaddress = Column(INET_TYPE)
    modelname = Column(String(60))
    isactive = Column(Boolean, default=True)
    floor = relationship("Floor", back_populates="accesspoints")
    room = relationship("Room", back_populates="accesspoints")
    clientcounts = relationship("ClientCountAP", back_populates="accesspoint", cascade="all, delete-orphan")

class RadioType(Base):
    __tablename__ = "radiotypes"
    radioid = Column(Integer, primary_key=True, autoincrement=True)
    radioname = Column(String(50), nullable=False)
    clientcounts = relationship("ClientCountAP", back_populates="radio", cascade="all, delete-orphan")

class ClientCountAP(Base):
    __tablename__ = "clientcount"
    countid = Column(Integer, primary_key=True, autoincrement=True)
    apid = Column(Integer, ForeignKey("accesspoints.apid", ondelete="CASCADE"))
    radioid = Column(Integer, ForeignKey("radiotypes.radioid", ondelete="CASCADE"))
    clientcount = Column(Integer, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    accesspoint = relationship("AccessPoint", back_populates="clientcounts")
    radio = relationship("RadioType", back_populates="clientcounts")

    __table_args__ = (
        {'sqlite_autoincrement': True},
    )