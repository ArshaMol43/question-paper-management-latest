from pydantic import BaseModel, EmailStr
from datetime import datetime


class UserCreate(BaseModel):

    name: str

    username: str

    email: EmailStr

    password: str

    user_type: str


class UserLogin(BaseModel):

    username: str

    password: str


class QuestionPaperCreate(BaseModel):

    department: str

    level: str

    semester: int

    subject: str

    paper_type: str

    year: int

    file_name: str | None = None

    file_path: str | None = None

    exam_date: datetime | None = None

    upload_date: datetime | None = None
    faculty_name: str | None = None


class SyllabusCreate(BaseModel):

    department: str

    level: str

    semester: int

    subject: str

    year: int

    file_name: str | None = None

    file_path: str | None = None

    upload_date: datetime | None = None


class ForgotPasswordRequest(BaseModel):

    email: EmailStr


class ResetPasswordRequest(BaseModel):

    token: str

    new_password: str
