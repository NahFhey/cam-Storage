"""Request models with validation."""
from typing import Annotated, Dict, List, Optional

from pydantic import AfterValidator, BaseModel, Field, field_validator

import config


def _one_of(allowed: List[str], label: str) -> AfterValidator:
    def check(value):
        if value is not None and value not in allowed:
            raise ValueError(f'{label} must be one of: {", ".join(allowed)}')
        return value
    return AfterValidator(check)


Station = Annotated[str, _one_of(config.STATIONS, "Station")]
DiePosition = Annotated[Optional[str], _one_of(config.DIE_POSITIONS, "Die position")]
Priority = Annotated[str, _one_of(config.PRIORITY_LEVELS, "Priority")]
Role = Annotated[str, _one_of(config.USER_ROLES, "Role")]
PriorityChangeSource = Annotated[Optional[str], _one_of(["all_jobs", "top5"], "Source")]

MaterialLife = Field(None, ge=0.001, le=2.0, description="Max material life in inches")
MaterialRemoved = Field(None, ge=0.0, le=1.0, description="Material removed in inches (0-1)")


# ========== Jobs ==========

class JobCreate(BaseModel):
    s_number: str = Field(..., min_length=1, max_length=50, description="Job S-number (e.g., 'S1793')")
    title: Optional[str] = Field(None, max_length=200)
    priority_level: Priority = "low"
    notes: Optional[str] = Field(None, max_length=1000)

    @field_validator('s_number')
    @classmethod
    def validate_s_number(cls, v):
        """S-numbers are stored as digits only (the 'S' prefix is optional on input)."""
        cleaned = v.upper().strip().lstrip('S')
        if not cleaned.isdigit():
            raise ValueError('S-number must be numeric (e.g., S1793 or 1793)')
        return cleaned


class JobUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    priority_level: Optional[Priority] = None
    notes: Optional[str] = Field(None, max_length=1000)
    reason: Optional[str] = Field(None, max_length=500, description="Required when changing priority")
    source: PriorityChangeSource = "all_jobs"


class Top5UpdateRequest(BaseModel):
    jobs: List[Dict] = Field(..., description="List of {job_id, priority_level, reason}")


class Top5ReorderRequest(BaseModel):
    job_ids: List[int] = Field(..., description="Ordered list of job IDs (position 1 first)")


# ========== CAM Items ==========

class CamItemCreate(BaseModel):
    job_id: int = Field(..., gt=0)
    set_no: int = Field(..., gt=0, le=999)
    cam_no: int = Field(..., gt=0, le=999)
    die_position: DiePosition = None
    enter_die_steel: Optional[str] = Field(None, max_length=50)
    exit_die_steel: Optional[str] = Field(None, max_length=50)
    status_station: Station = "cabinet"
    notes: Optional[str] = Field(None, max_length=1000)
    eol_cycles_expected: Optional[int] = Field(None, ge=0)
    max_material_life: Optional[float] = MaterialLife


class CamItemUpdate(BaseModel):
    die_position: DiePosition = None
    enter_die_steel: Optional[str] = Field(None, max_length=50)
    exit_die_steel: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = Field(None, max_length=1000)
    eol_cycles_expected: Optional[int] = Field(None, ge=0)
    max_material_life: Optional[float] = MaterialLife


class CamConfig(BaseModel):
    cam_no: int = Field(..., gt=0, le=999)
    die_position: DiePosition = None
    enter_die_steel: Optional[str] = Field(None, max_length=50)
    exit_die_steel: Optional[str] = Field(None, max_length=50)


class CamItemBulkCreate(BaseModel):
    job_id: int = Field(..., gt=0)
    num_sets: int = Field(..., gt=0, le=100, description="Number of sets to create (1..num_sets)")
    cams: List[CamConfig] = Field(..., min_length=1, max_length=100)

    @field_validator('cams')
    @classmethod
    def validate_cams(cls, v):
        cam_nos = [c.cam_no for c in v]
        if len(cam_nos) != len(set(cam_nos)):
            raise ValueError('CAM numbers must be unique')
        return v


# ========== Moves ==========

class MoveRequest(BaseModel):
    cam_item_id: int = Field(..., gt=0)
    to_station: Station
    operator: Optional[str] = Field(None, max_length=100)  # Ignored: the logged-in user is recorded
    notes: Optional[str] = Field(None, max_length=500)
    auto_bump: Optional[bool] = None
    material_removed: Optional[float] = MaterialRemoved


class BatchMoveItem(BaseModel):
    cam_item_id: int = Field(..., gt=0)
    to_station: Station
    notes: Optional[str] = Field(None, max_length=500)
    material_removed: Optional[float] = MaterialRemoved


class BatchMoveRequest(BaseModel):
    moves: List[BatchMoveItem] = Field(..., min_length=1, max_length=50)
    auto_bump: Optional[bool] = None


class SetMoveRequest(BaseModel):
    job_id: int = Field(..., gt=0)
    set_no: int = Field(..., gt=0)
    to_station: Station
    material_removed: Dict[int, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(
        default_factory=dict, description="cam_item_id -> inches, for sharpen->cabinet moves"
    )


class EntryResolveRequest(BaseModel):
    entry: str = Field(..., min_length=1, max_length=100)


# ========== Config & Users ==========

class ConfigUpdate(BaseModel):
    auto_bump_enabled: Optional[bool] = None
    default_material_life: Optional[float] = MaterialLife


class LoginRequest(BaseModel):
    pin: str = Field(..., min_length=1, max_length=20)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    display_name: str = Field(..., min_length=1, max_length=100)
    pin: str = Field(..., min_length=4, max_length=20)
    role: Role = "user"

    @field_validator('username')
    @classmethod
    def validate_username(cls, v):
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Username must be alphanumeric (underscores and hyphens allowed)')
        return v.lower()


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    pin: Optional[str] = Field(None, min_length=4, max_length=20)
    role: Optional[Role] = None
    active: Optional[bool] = None
