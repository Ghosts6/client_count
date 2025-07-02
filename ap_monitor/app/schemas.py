from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from decimal import Decimal

# Wireless Count Schemas
class CampusBase(BaseModel):
    campus_name: str = Field(..., description="Name of the campus")

class CampusCreate(CampusBase):
    pass

class CampusResponse(CampusBase):
    campus_id: int
    model_config = ConfigDict(from_attributes=True)

class BuildingBase(BaseModel):
    building_name: str = Field(..., description="Name of the building")
    campus_id: int = Field(..., description="ID of the campus this building belongs to")
    latitude: Decimal = Field(..., description="Latitude coordinate of the building")
    longitude: Decimal = Field(..., description="Longitude coordinate of the building")

class BuildingCreate(BuildingBase):
    pass

class BuildingResponse(BuildingBase):
    building_id: int
    model_config = ConfigDict(from_attributes=True)

class ClientCountBase(BaseModel):
    building_id: int = Field(..., description="ID of the building")
    client_count: int = Field(..., description="Number of clients")

class ClientCountCreate(ClientCountBase):
    pass

class ClientCountResponse(ClientCountBase):
    count_id: int
    time_inserted: datetime
    model_config = ConfigDict(from_attributes=True)

# AP Client Count Schemas
class ApBuildingBase(BaseModel):
    buildingname: str = Field(..., description="Name of the building")

class ApBuildingCreate(ApBuildingBase):
    pass

class ApBuildingResponse(ApBuildingBase):
    buildingid: int
    model_config = ConfigDict(from_attributes=True)

class FloorBase(BaseModel):
    floorname: str = Field(..., description="Name of the floor")
    buildingid: int = Field(..., description="ID of the building this floor belongs to")

class FloorCreate(FloorBase):
    pass

class FloorResponse(FloorBase):
    floorid: int
    model_config = ConfigDict(from_attributes=True)

class RoomBase(BaseModel):
    roomname: str = Field(..., description="Name of the room")
    floorid: int = Field(..., description="ID of the floor this room belongs to")

class RoomCreate(RoomBase):
    pass

class RoomResponse(RoomBase):
    roomid: int
    model_config = ConfigDict(from_attributes=True)

class AccessPointBase(BaseModel):
    apname: str = Field(..., description="Name of the access point")
    macaddress: str = Field(..., description="MAC address of the access point")
    ipaddress: Optional[str] = Field(None, description="IP address of the access point")
    modelname: Optional[str] = Field(None, description="Model name of the access point")
    isactive: bool = Field(True, description="Whether the access point is active")
    buildingid: int = Field(..., description="ID of the building")
    floorid: int = Field(..., description="ID of the floor")
    roomid: Optional[int] = Field(None, description="ID of the room")

class AccessPointCreate(AccessPointBase):
    pass

class AccessPointResponse(AccessPointBase):
    apid: int
    model_config = ConfigDict(from_attributes=True)

class RadioTypeBase(BaseModel):
    radioname: str = Field(..., description="Name of the radio type")

class RadioTypeCreate(RadioTypeBase):
    pass

class RadioTypeResponse(RadioTypeBase):
    radioid: int
    model_config = ConfigDict(from_attributes=True)

class ClientCountAPBase(BaseModel):
    apid: Optional[int] = Field(None, description="ID of the access point")
    radioid: int = Field(..., description="ID of the radio type")
    clientcount: int = Field(..., description="Number of clients")
    timestamp: datetime = Field(..., description="Timestamp of the client count")

class ClientCountAPCreate(ClientCountAPBase):
    pass

class ClientCountAPResponse(ClientCountAPBase):
    countid: int
    model_config = ConfigDict(from_attributes=True)