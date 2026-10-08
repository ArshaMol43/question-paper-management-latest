from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)
    user_type = Column(String, nullable=False)


class QuestionPaper(Base):
    __tablename__ = "question_papers"

    id = Column(Integer, primary_key=True, index=True)

    department = Column(String, nullable=False)
    level = Column(String, nullable=False)
    semester = Column(Integer, nullable=False)
    subject = Column(String, nullable=False)
    paper_type = Column(String, nullable=False)
    year = Column(Integer, nullable=False)

    file_name = Column(String, nullable=True)
    file_path = Column(String, nullable=True)

    # Upcoming paper details
    exam_date = Column(DateTime, nullable=True)
    upload_date = Column(DateTime, nullable=True)
    faculty_name = Column(String, nullable=True)


class Syllabus(Base):
    __tablename__ = "syllabus"

    id = Column(Integer, primary_key=True, index=True)

    department = Column(String, nullable=False)
    level = Column(String, nullable=False)
    semester = Column(Integer, nullable=False)
    subject = Column(String, nullable=False)
    year = Column(Integer, nullable=False)

    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)

    upload_date = Column(DateTime, nullable=True)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False
    )

    token = Column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    expires_at = Column(
        DateTime,
        nullable=False
    )

    used = Column(
        Boolean,
        default=False,
        nullable=False
    )
class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)


class QuestionPaperFormat(Base):
    __tablename__ = "question_paper_formats"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String, nullable=False)
    format_type = Column(String, nullable=False)
    format_content = Column(String, nullable=True)
    file_name = Column(String, nullable=True)
    file_path = Column(String, nullable=True)
    updated_at = Column(DateTime, nullable=True)
