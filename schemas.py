import re
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
    "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR".split()
)
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z' \-]{0,49}$")
FULLNAME_RE = re.compile(r"^[A-Za-z][A-Za-z.' \-]{0,99}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
MEMBER_RE = re.compile(r"^[A-Za-z0-9]{1,50}$")
SEX_ALIASES = {
    "male": "Male", "female": "Female", "other": "Other",
    "decline to answer": "Decline to Answer", "decline": "Decline to Answer",
    "prefer not to say": "Decline to Answer",
}


class Sex(str, Enum):
    MALE = "Male"
    FEMALE = "Female"
    OTHER = "Other"
    DECLINE = "Decline to Answer"


def normalize_phone(v) -> str:
    """Return a bare 10-digit US number or raise ValueError."""
    digits = re.sub(r"\D", "", str(v))
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01":
        raise ValueError("must be a valid 10-digit U.S. phone number (area code + 7 digits)")
    return digits


def parse_dob(v: str) -> date:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError("must be a real date in MM/DD/YYYY format")


class _Rules(BaseModel):
    """Shared sanitising + validation rules. Used by create and update models."""
    model_config = ConfigDict(use_enum_values=True)

    @model_validator(mode="before")
    @classmethod
    def _clean(cls, data):
        # Basic sanitisation: strip control chars and angle brackets, blank -> None
        if isinstance(data, dict):
            out = {}
            for k, v in data.items():
                if isinstance(v, str):
                    v = re.sub(r"[<>\x00-\x1f]", "", v).strip() or None
                out[k] = v
            return out
        return data

    @field_validator("first_name", "last_name", check_fields=False)
    @classmethod
    def _name(cls, v):
        if v is not None and not NAME_RE.match(v):
            raise ValueError("must be 1-50 letters, hyphens or apostrophes")
        return v

    @field_validator("emergency_contact_name", check_fields=False)
    @classmethod
    def _full_name(cls, v):
        if v is not None and not FULLNAME_RE.match(v):
            raise ValueError("must be a valid full name (letters, hyphens, apostrophes)")
        return v

    @field_validator("date_of_birth", mode="before", check_fields=False)
    @classmethod
    def _dob_parse(cls, v):
        return parse_dob(v) if isinstance(v, str) else v

    @field_validator("date_of_birth", check_fields=False)
    @classmethod
    def _dob_range(cls, v):
        if v is not None:
            if v > date.today():
                raise ValueError("cannot be in the future")
            if v.year < 1900:
                raise ValueError("year is implausibly old")
        return v

    @field_validator("sex", mode="before", check_fields=False)
    @classmethod
    def _sex(cls, v):
        return SEX_ALIASES.get(v.strip().lower(), v) if isinstance(v, str) else v

    @field_validator("phone_number", "emergency_contact_phone", mode="before", check_fields=False)
    @classmethod
    def _phone(cls, v):
        return None if v is None else normalize_phone(v)

    @field_validator("state", check_fields=False)
    @classmethod
    def _state(cls, v):
        if v is not None:
            v = v.upper()
            if v not in US_STATES:
                raise ValueError("must be a valid 2-letter U.S. state abbreviation")
        return v

    @field_validator("zip_code", check_fields=False)
    @classmethod
    def _zip(cls, v):
        if v is not None and not ZIP_RE.match(v):
            raise ValueError("must be 5 digits or ZIP+4 (12345 or 12345-6789)")
        return v

    @field_validator("insurance_member_id", mode="before", check_fields=False)
    @classmethod
    def _member(cls, v):
        if v is None:
            return v
        v = re.sub(r"[\s\-]", "", str(v))
        if not MEMBER_RE.match(v):
            raise ValueError("must be alphanumeric")
        return v


class PatientCreate(_Rules):
    first_name: str
    last_name: str
    date_of_birth: date
    sex: Sex
    phone_number: str
    email: Optional[EmailStr] = None
    address_line_1: str = Field(max_length=200)
    address_line_2: Optional[str] = Field(None, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    state: str
    zip_code: str
    insurance_provider: Optional[str] = Field(None, max_length=100)
    insurance_member_id: Optional[str] = None
    preferred_language: str = Field("English", max_length=50)
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None


class PatientUpdate(_Rules):
    """Every field optional -> partial updates."""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    sex: Optional[Sex] = None
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None
    address_line_1: Optional[str] = Field(None, max_length=200)
    address_line_2: Optional[str] = Field(None, max_length=100)
    city: Optional[str] = Field(None, min_length=1, max_length=100)
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = Field(None, max_length=100)
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = Field(None, max_length=50)
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None