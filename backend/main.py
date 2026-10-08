from fastapi.staticfiles import StaticFiles
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import engine, Base, SessionLocal
import models
from schemas import (
    UserCreate,
    UserLogin,
    QuestionPaperCreate,
    SyllabusCreate,
    ForgotPasswordRequest,
    ResetPasswordRequest
)
from pwdlib import PasswordHash
import os
import secrets
import mimetypes
from datetime import datetime, timedelta

app = FastAPI(title="Question Paper Management System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

password_hash = PasswordHash.recommended()
Base.metadata.create_all(bind=engine)


def migrate_format_file_columns():
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    try:
        columns = {c["name"] for c in inspector.get_columns("question_paper_formats")}
    except Exception:
        return
    with engine.begin() as connection:
        if "file_name" not in columns:
            connection.execute(text("ALTER TABLE question_paper_formats ADD COLUMN file_name VARCHAR"))
        if "file_path" not in columns:
            connection.execute(text("ALTER TABLE question_paper_formats ADD COLUMN file_path VARCHAR"))

migrate_format_file_columns()


def faculty_name_migration():
    """Add the new optional faculty_name column without changing existing rows."""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("question_papers")}
    if "faculty_name" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE question_papers ADD COLUMN faculty_name VARCHAR"))


faculty_name_migration()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# DATABASE
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# HOME
@app.get("/")
def home():
    return FileResponse(FRONTEND_DIR / "index.html")


# REGISTER
@app.post("/register")
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    existing_username = db.query(models.User).filter(
        models.User.username == user.username
    ).first()

    if existing_username:
        raise HTTPException(status_code=400, detail="Username already exists")

    existing_email = db.query(models.User).filter(
        models.User.email == user.email
    ).first()

    if existing_email:
        raise HTTPException(status_code=400, detail="Email already exists")

    new_user = models.User(
        name=user.name,
        username=user.username,
        email=user.email,
        password=password_hash.hash(user.password),
        user_type=user.user_type
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "message": "Account created successfully",
        "user_id": new_user.id,
        "username": new_user.username,
        "user_type": new_user.user_type
    }


# LOGIN
@app.post("/login")
def login_user(user: UserLogin, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(
        models.User.username == user.username
    ).first()

    if not existing_user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not password_hash.verify(user.password, existing_user.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {
        "message": "Login successful",
        "user_id": existing_user.id,
        "username": existing_user.username,
        "user_type": existing_user.user_type
    }


# USERS
@app.get("/users")
def get_users(db: Session = Depends(get_db)):
    users = db.query(models.User).all()

    return [
        {
            "id": user.id,
            "name": user.name,
            "username": user.username,
            "email": user.email,
            "user_type": user.user_type
        }
        for user in users
    ]


# DELETE USER
@app.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.delete(user)
    db.commit()

    return {"message": "User deleted successfully", "user_id": user_id}


# FORGOT PASSWORD
@app.post("/forgot-password")
def forgot_password(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(
        models.User.email == request.email
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="Email address not found")

    old_tokens = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.user_id == user.id
    ).all()

    for old_token in old_tokens:
        db.delete(old_token)

    token = secrets.token_urlsafe(32)
    expiry_time = datetime.now() + timedelta(minutes=15)

    reset_token = models.PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=expiry_time,
        used=False
    )

    db.add(reset_token)
    db.commit()

    reset_link = "reset_password.html?token=" + token

    return {
        "message": "Password reset link generated successfully.",
        "reset_link": reset_link
    }


# RESET PASSWORD
@app.post("/reset-password")
def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db)
):
    reset_token = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token == request.token
    ).first()

    if not reset_token:
        raise HTTPException(status_code=400, detail="Invalid reset link")

    if reset_token.used:
        raise HTTPException(
            status_code=400,
            detail="This reset link has already been used"
        )

    if datetime.now() > reset_token.expires_at:
        raise HTTPException(status_code=400, detail="This reset link has expired")

    user = db.query(models.User).filter(
        models.User.id == reset_token.user_id
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password = password_hash.hash(request.new_password)
    reset_token.used = True
    db.commit()

    return {"message": "Password reset successfully"}


# =========================================================
# QUESTION PAPER MANAGEMENT
# =========================================================

ALLOWED_PAPER_TYPES = {
    "Internal 1",
    "Internal 2",
    "Internal 3",
    "Model",
    "University"
}

# Legacy value kept only for displaying old database records.
LEGACY_PAPER_TYPES = {"Internal"}

ALLOWED_MANAGERS = {"faculty", "admin"}


def normalize_user_type(user_type: str | None) -> str:
    return (user_type or "student").strip().lower()


def require_manager(user_type: str | None):
    role = normalize_user_type(user_type)
    if role not in ALLOWED_MANAGERS:
        raise HTTPException(
            status_code=403,
            detail="Only Faculty or Admin can manage question papers."
        )
    return role


def check_paper_access(paper, user_type: str | None = "student"):
    """Students can access a scheduled paper only after its exam date/time.
    Faculty and Admin can view/download before and after the exam.
    """
    role = normalize_user_type(user_type)

    if role not in {"student", "faculty", "admin"}:
        raise HTTPException(status_code=403, detail="Invalid user type")

    if role == "student" and paper.exam_date is not None:
        if paper.exam_date > datetime.now():
            raise HTTPException(
                status_code=403,
                detail="Question paper is locked until the exam date and time."
            )


def check_paper_edit_access(paper, user_type: str | None):
    require_manager(user_type)

    if paper.exam_date is not None and paper.exam_date <= datetime.now():
        raise HTTPException(
            status_code=403,
            detail="This question paper can no longer be edited or deleted because the exam date and time has passed."
        )


def save_upload(file: UploadFile, upload_folder: str):
    allowed = {".pdf", ".doc", ".docx"}
    original_name = os.path.basename(file.filename or "uploaded_file.pdf")
    extension = Path(original_name).suffix.lower()
    allowed_mimes = {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    }
    if extension not in allowed:
        raise HTTPException(status_code=400, detail="Only PDF, DOC or DOCX files are allowed")
    if file.content_type and file.content_type not in allowed_mimes:
        raise HTTPException(status_code=400, detail="Only PDF, DOC or DOCX files are allowed")
    os.makedirs(upload_folder, exist_ok=True)
    stored_name = f"{secrets.token_hex(8)}_{original_name}"
    return original_name, os.path.join(upload_folder, stored_name)


def validate_paper_data(paper_type: str, exam_date):
    if paper_type not in ALLOWED_PAPER_TYPES:
        raise HTTPException(status_code=400, detail="Invalid question paper type")

    # Internal examinations must have an exact exam date and time because
    # student access is released only after that date/time.
    if paper_type in {"Internal 1", "Internal 2", "Internal 3"} and exam_date is None:
        raise HTTPException(
            status_code=400,
            detail="Exam date and time are required for internal question papers."
        )


def resolve_paper_file_path(file_path: str | None):
    if not file_path:
        return None

    if os.path.exists(file_path):
        return file_path

    # Older database rows stored paths such as uploads\\file.pdf.
    candidate = Path(__file__).resolve().parent / Path(file_path.replace("\\", os.sep))
    if candidate.exists():
        return str(candidate)

    return None


def media_type_for_file(file_name: str | None) -> str | None:
    if not file_name:
        return None
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


def paper_to_dict(paper, role: str):
    now = datetime.now()
    upcoming = paper.exam_date is not None and paper.exam_date > now
    exam_passed = paper.exam_date is not None and paper.exam_date <= now
    can_manage = role in ALLOWED_MANAGERS and not exam_passed
    # Delete is intentionally allowed for Admin/Faculty even after the exam.
    # Once deleted from the database, the paper disappears for every role.
    can_delete = role in ALLOWED_MANAGERS
    student_visible = paper.exam_date is None or exam_passed

    return {
        "id": paper.id,
        "department": paper.department,
        "level": paper.level,
        "semester": paper.semester,
        "subject": paper.subject,
        "paper_type": paper.paper_type,
        "year": paper.year,
        "file_name": paper.file_name,
        "file_path": paper.file_path,
        "exam_date": paper.exam_date,
        "upload_date": paper.upload_date,
        "faculty_name": getattr(paper, "faculty_name", None),
        "status": "Upcoming" if upcoming else ("Exam completed" if exam_passed else "Available"),
        "exam_passed": exam_passed,
        "upcoming": upcoming,
        "can_manage": can_manage,
        "can_delete": can_delete,
        "student_visible": student_visible,
    }


# CREATE QUESTION PAPER (JSON endpoint retained for compatibility)
@app.post("/question-papers")
def create_question_paper(
    paper: QuestionPaperCreate,
    user_type: str = "admin",
    db: Session = Depends(get_db)
):
    require_manager(user_type)

    validate_paper_data(paper.paper_type, paper.exam_date)

    new_paper = models.QuestionPaper(
        department=paper.department,
        level=paper.level,
        semester=paper.semester,
        subject=paper.subject,
        paper_type=paper.paper_type,
        year=paper.year,
        file_name=paper.file_name,
        file_path=paper.file_path,
        exam_date=paper.exam_date,
        upload_date=paper.upload_date or datetime.now(),
        faculty_name=paper.faculty_name
    )

    db.add(new_paper)
    db.commit()
    db.refresh(new_paper)

    return {
        "message": "Question paper added successfully",
        "paper_id": new_paper.id
    }


# GET QUESTION PAPERS
@app.get("/question-papers")
def get_question_papers(
    department: str | None = None,
    level: str | None = None,
    semester: int | None = None,
    subject: str | None = None,
    paper_type: str | None = None,
    year: int | None = None,
    user_type: str = "student",
    db: Session = Depends(get_db)
):
    role = normalize_user_type(user_type)

    if role not in {"student", "faculty", "admin"}:
        raise HTTPException(status_code=403, detail="Invalid user type")

    query = db.query(models.QuestionPaper)

    if department:
        query = query.filter(models.QuestionPaper.department == department)
    if level:
        query = query.filter(models.QuestionPaper.level == level)
    if semester:
        query = query.filter(models.QuestionPaper.semester == semester)
    if subject:
        query = query.filter(models.QuestionPaper.subject.ilike(f"%{subject}%"))
    if paper_type:
        query = query.filter(models.QuestionPaper.paper_type == paper_type)
    if year:
        query = query.filter(models.QuestionPaper.year == year)

    # Students see a paper only after its scheduled exam date/time.
    # Papers without an exam date (for example Model/University papers) remain visible.
    if role == "student":
        now = datetime.now()
        query = query.filter(
            (models.QuestionPaper.exam_date == None) |
            (models.QuestionPaper.exam_date <= now)
        )

    papers = query.order_by(models.QuestionPaper.id.desc()).all()
    return [paper_to_dict(paper, role) for paper in papers]


# UPCOMING QUESTION PAPERS
@app.get("/question-papers/upcoming")
def get_upcoming_question_papers(
    user_type: str = "faculty",
    db: Session = Depends(get_db)
):
    require_manager(user_type)

    current_time = datetime.now()

    papers = db.query(models.QuestionPaper).filter(
        models.QuestionPaper.exam_date != None,
        models.QuestionPaper.exam_date > current_time
    ).order_by(models.QuestionPaper.exam_date.asc()).all()

    return [paper_to_dict(paper, normalize_user_type(user_type)) for paper in papers]


# UPLOAD QUESTION PAPER - Faculty and Admin
@app.post("/upload-question-paper")
async def upload_question_paper(
    department: str = Form(...),
    level: str = Form(...),
    semester: int = Form(...),
    subject: str = Form(...),
    paper_type: str = Form(...),
    year: int = Form(...),
    exam_date: datetime | None = Form(None),
    faculty_name: str | None = Form(None),
    user_type: str = Form("faculty"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    require_manager(user_type)

    validate_paper_data(paper_type, exam_date)

    upload_folder = str(Path(__file__).resolve().parent / "uploads")
    os.makedirs(upload_folder, exist_ok=True)

    original_name, file_path = save_upload(file, upload_folder)

    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())

    new_paper = models.QuestionPaper(
        department=department,
        level=level,
        semester=semester,
        subject=subject,
        paper_type=paper_type,
        year=year,
        file_name=original_name,
        file_path=file_path,
        exam_date=exam_date,
        upload_date=datetime.now(),
        faculty_name=(faculty_name.strip() if faculty_name else None)
    )

    db.add(new_paper)
    db.commit()
    db.refresh(new_paper)

    return {
        "message": "Question paper uploaded successfully",
        "paper_id": new_paper.id,
        "file_name": original_name,
        "exam_date": exam_date
    }


# UPDATE / REPLACE QUESTION PAPER - Faculty and Admin, only before exam
@app.put("/question-papers/{paper_id}")
async def update_question_paper(
    paper_id: int,
    department: str = Form(...),
    level: str = Form(...),
    semester: int = Form(...),
    subject: str = Form(...),
    paper_type: str = Form(...),
    year: int = Form(...),
    exam_date: datetime | None = Form(None),
    faculty_name: str | None = Form(None),
    user_type: str = Form("faculty"),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db)
):
    paper = db.query(models.QuestionPaper).filter(
        models.QuestionPaper.id == paper_id
    ).first()

    if not paper:
        raise HTTPException(status_code=404, detail="Question paper not found")

    check_paper_edit_access(paper, user_type)

    validate_paper_data(paper_type, exam_date)

    paper.department = department
    paper.level = level
    paper.semester = semester
    paper.subject = subject
    paper.paper_type = paper_type
    paper.year = year
    paper.exam_date = exam_date
    if faculty_name is not None and faculty_name.strip():
        paper.faculty_name = faculty_name.strip()

    if file is not None and file.filename:
        upload_folder = str(Path(__file__).resolve().parent / "uploads")
        os.makedirs(upload_folder, exist_ok=True)

        original_name, new_file_path = save_upload(file, upload_folder)

        with open(new_file_path, "wb") as buffer:
            buffer.write(await file.read())

        old_file_path = paper.file_path
        paper.file_name = original_name
        paper.file_path = new_file_path

        if (
            old_file_path
            and os.path.exists(old_file_path)
            and old_file_path != new_file_path
        ):
            os.remove(old_file_path)

    db.commit()
    db.refresh(paper)

    return {
        "message": "Question paper updated successfully",
        "paper_id": paper.id,
        "file_name": paper.file_name,
        "exam_date": paper.exam_date
    }


# DOWNLOAD QUESTION PAPER
@app.get("/question-papers/{paper_id}/download")
def download_question_paper(
    paper_id: int,
    user_type: str = "student",
    db: Session = Depends(get_db)
):
    paper = db.query(models.QuestionPaper).filter(
        models.QuestionPaper.id == paper_id
    ).first()

    if not paper:
        raise HTTPException(status_code=404, detail="Question paper not found")

    check_paper_access(paper, user_type)

    resolved_path = resolve_paper_file_path(paper.file_path)
    if not resolved_path:
        raise HTTPException(status_code=404, detail="Question paper file not found")

    return FileResponse(
        path=resolved_path,
        filename=paper.file_name,
        media_type="application/pdf"
    )


# VIEW QUESTION PAPER
@app.get("/question-papers/{paper_id}/view")
def view_question_paper(
    paper_id: int,
    user_type: str = "student",
    db: Session = Depends(get_db)
):
    paper = db.query(models.QuestionPaper).filter(
        models.QuestionPaper.id == paper_id
    ).first()

    if not paper:
        raise HTTPException(status_code=404, detail="Question paper not found")

    check_paper_access(paper, user_type)

    resolved_path = resolve_paper_file_path(paper.file_path)
    if not resolved_path:
        raise HTTPException(status_code=404, detail="Question paper file not found")

    return FileResponse(
        path=resolved_path,
        media_type="application/pdf",
        content_disposition_type="inline"
    )


# DELETE QUESTION PAPER - Faculty and Admin, only before exam
@app.delete("/question-papers/{paper_id}")
def delete_question_paper(
    paper_id: int,
    user_type: str = "faculty",
    db: Session = Depends(get_db)
):
    paper = db.query(models.QuestionPaper).filter(
        models.QuestionPaper.id == paper_id
    ).first()

    if not paper:
        raise HTTPException(status_code=404, detail="Question paper not found")

    # Deletion is different from editing: Admin and Faculty can delete
    # ANY question paper, even after the exam. This removes the database
    # record, so it is no longer returned to Admin, Faculty, or Students.
    require_manager(user_type)

    resolved_path = resolve_paper_file_path(paper.file_path)
    if resolved_path and os.path.exists(resolved_path):
        os.remove(resolved_path)

    db.delete(paper)
    db.commit()

    return {
        "message": "Question paper deleted successfully",
        "paper_id": paper_id
    }



# =========================================================
# QUESTION PAPER FORMAT (FACULTY ONLY)
# =========================================================

ALLOWED_FORMAT_TYPES = {"Internal", "Model", "University"}
ALLOWED_LEVELS = {"UG", "PG"}


def require_faculty(user_type: str | None):
    if normalize_user_type(user_type) != "faculty":
        raise HTTPException(
            status_code=403,
            detail="Question paper format is available only to Faculty."
        )


@app.get("/question-paper-formats")
def get_question_paper_formats(user_type: str = "student", db: Session = Depends(get_db)):
    require_faculty(user_type)
    formats = db.query(models.QuestionPaperFormat).order_by(
        models.QuestionPaperFormat.level.asc(),
        models.QuestionPaperFormat.format_type.asc()
    ).all()
    return [
        {
            "id": item.id,
            "level": item.level,
            "format_type": item.format_type,
            "format_content": item.format_content,
            "file_name": getattr(item, "file_name", None),
            "file_path": getattr(item, "file_path", None),
            "has_file": bool(getattr(item, "file_path", None)),
            "updated_at": item.updated_at,
        } for item in formats
    ]


@app.post("/question-paper-formats")
async def create_question_paper_format(
    level: str = Form(...),
    format_type: str = Form(...),
    user_type: str = Form("student"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    require_faculty(user_type)
    level = level.strip().upper()
    format_type = format_type.strip().title()
    if level not in ALLOWED_LEVELS:
        raise HTTPException(status_code=400, detail="Level must be UG or PG.")
    if format_type not in ALLOWED_FORMAT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid question paper format type.")

    upload_folder = str(Path(__file__).resolve().parent / "uploads" / "formats")
    original_name, file_path = save_upload(file, upload_folder)
    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())

    existing = db.query(models.QuestionPaperFormat).filter(
        models.QuestionPaperFormat.level == level,
        models.QuestionPaperFormat.format_type == format_type
    ).first()

    if existing:
        old_path = getattr(existing, "file_path", None)
        existing.format_content = None
        existing.file_name = original_name
        existing.file_path = file_path
        existing.updated_at = datetime.now()
        db.commit()
        if old_path and os.path.exists(old_path) and old_path != file_path:
            os.remove(old_path)
        return {"message": "Question paper format updated successfully", "format_id": existing.id}

    new_format = models.QuestionPaperFormat(
        level=level,
        format_type=format_type,
        format_content=None,
        file_name=original_name,
        file_path=file_path,
        updated_at=datetime.now()
    )
    db.add(new_format)
    db.commit()
    db.refresh(new_format)
    return {"message": "Question paper format uploaded successfully", "format_id": new_format.id}


@app.put("/question-paper-formats/{format_id}")
async def update_question_paper_format(
    format_id: int,
    level: str = Form(...),
    format_type: str = Form(...),
    user_type: str = Form("student"),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db)
):
    require_faculty(user_type)
    item = db.query(models.QuestionPaperFormat).filter(models.QuestionPaperFormat.id == format_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Question paper format not found.")
    level = level.strip().upper()
    format_type = format_type.strip().title()
    if level not in ALLOWED_LEVELS or format_type not in ALLOWED_FORMAT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid level or format type.")
    duplicate = db.query(models.QuestionPaperFormat).filter(
        models.QuestionPaperFormat.id != format_id,
        models.QuestionPaperFormat.level == level,
        models.QuestionPaperFormat.format_type == format_type
    ).first()
    if duplicate:
        raise HTTPException(status_code=400, detail="This level and format type already exists.")
    item.level = level
    item.format_type = format_type
    if file is not None and file.filename:
        upload_folder = str(Path(__file__).resolve().parent / "uploads" / "formats")
        original_name, new_file_path = save_upload(file, upload_folder)
        with open(new_file_path, "wb") as buffer:
            buffer.write(await file.read())
        old_path = getattr(item, "file_path", None)
        item.format_content = None
        item.file_name = original_name
        item.file_path = new_file_path
        if old_path and os.path.exists(old_path) and old_path != new_file_path:
            os.remove(old_path)
    item.updated_at = datetime.now()
    db.commit()
    return {"message": "Question paper format updated successfully"}

import mimetypes


def media_type_for_file(filename: str) -> str:
    media_type, _ = mimetypes.guess_type(filename)
    return media_type or "application/octet-stream"

@app.get("/question-paper-formats/{format_id}/download")
def download_question_paper_format(format_id: int, user_type: str = "faculty", db: Session = Depends(get_db)):
    require_faculty(user_type)
    item = db.query(models.QuestionPaperFormat).filter(models.QuestionPaperFormat.id == format_id).first()
    path = resolve_paper_file_path(getattr(item, "file_path", None)) if item else None
    if not item or not path:
        raise HTTPException(status_code=404, detail="Question paper format file not found")
    return FileResponse(path=path, filename=item.file_name, media_type=media_type_for_file(item.file_name))


@app.get("/question-paper-formats/{format_id}/view")
def view_question_paper_format(format_id: int, user_type: str = "faculty", db: Session = Depends(get_db)):
    require_faculty(user_type)
    item = db.query(models.QuestionPaperFormat).filter(models.QuestionPaperFormat.id == format_id).first()
    path = resolve_paper_file_path(getattr(item, "file_path", None)) if item else None
    if not item or not path:
        raise HTTPException(status_code=404, detail="Question paper format file not found")
    return FileResponse(path=path, filename=item.file_name, media_type=media_type_for_file(item.file_name))


# CREATE SYLLABUS
@app.post("/syllabus")
def create_syllabus(
    syllabus: SyllabusCreate,
    db: Session = Depends(get_db)
):
    new_syllabus = models.Syllabus(
        department=syllabus.department,
        level=syllabus.level,
        semester=syllabus.semester,
        subject=syllabus.subject,
        year=syllabus.year,
        file_name=syllabus.file_name,
        file_path=syllabus.file_path,
        upload_date=syllabus.upload_date
    )

    db.add(new_syllabus)
    db.commit()
    db.refresh(new_syllabus)

    return {
        "message": "Syllabus added successfully",
        "syllabus_id": new_syllabus.id
    }


# GET SYLLABUS
@app.get("/syllabus")
def get_syllabus(
    department: str | None = None,
    level: str | None = None,
    semester: int | None = None,
    subject: str | None = None,
    year: int | None = None,
    db: Session = Depends(get_db)
):
    query = db.query(models.Syllabus)

    if department:
        query = query.filter(models.Syllabus.department == department)
    if level:
        query = query.filter(models.Syllabus.level == level)
    if semester:
        query = query.filter(models.Syllabus.semester == semester)
    if subject:
        query = query.filter(models.Syllabus.subject == subject)
    if year:
        query = query.filter(models.Syllabus.year == year)

    return query.order_by(models.Syllabus.upload_date.desc()).all()


# UPLOAD SYLLABUS
@app.post("/upload-syllabus")
async def upload_syllabus(
    department: str = Form(...),
    level: str = Form(...),
    semester: int = Form(...),
    subject: str = Form(...),
    year: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    original_name, file_path = save_upload(file, "uploads")
    safe_filename = original_name

    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())

    new_syllabus = models.Syllabus(
        department=department,
        level=level,
        semester=semester,
        subject=subject,
        year=year,
        file_name=safe_filename,
        file_path=file_path,
        upload_date=datetime.now()
    )

    db.add(new_syllabus)
    db.commit()
    db.refresh(new_syllabus)

    return {
        "message": "Syllabus uploaded successfully",
        "syllabus_id": new_syllabus.id,
        "file_name": safe_filename
    }


# DOWNLOAD SYLLABUS
@app.get("/syllabus/{syllabus_id}/download")
def download_syllabus(
    syllabus_id: int,
    db: Session = Depends(get_db)
):
    syllabus = db.query(models.Syllabus).filter(
        models.Syllabus.id == syllabus_id
    ).first()

    if not syllabus:
        raise HTTPException(status_code=404, detail="Syllabus not found")

    if not syllabus.file_path or not os.path.exists(syllabus.file_path):
        raise HTTPException(status_code=404, detail="Syllabus file not found")

    return FileResponse(
        path=syllabus.file_path,
        filename=syllabus.file_name,
        media_type="application/pdf"
    )


# VIEW SYLLABUS
@app.get("/syllabus/{syllabus_id}/view")
def view_syllabus(
    syllabus_id: int,
    db: Session = Depends(get_db)
):
    syllabus = db.query(models.Syllabus).filter(
        models.Syllabus.id == syllabus_id
    ).first()

    if not syllabus:
        raise HTTPException(status_code=404, detail="Syllabus not found")

    if not syllabus.file_path or not os.path.exists(syllabus.file_path):
        raise HTTPException(status_code=404, detail="Syllabus file not found")

    return FileResponse(
        path=syllabus.file_path,
        media_type="application/pdf",
        content_disposition_type="inline"
    )


# DELETE SYLLABUS
@app.delete("/syllabus/{syllabus_id}")
def delete_syllabus(
    syllabus_id: int,
    db: Session = Depends(get_db)
):
    syllabus = db.query(models.Syllabus).filter(
        models.Syllabus.id == syllabus_id
    ).first()

    if not syllabus:
        raise HTTPException(status_code=404, detail="Syllabus not found")

    if syllabus.file_path and os.path.exists(syllabus.file_path):
        os.remove(syllabus.file_path)

    db.delete(syllabus)
    db.commit()

    return {
        "message": "Syllabus deleted successfully",
        "syllabus_id": syllabus_id
    }


# ADD DEPARTMENT
@app.post("/departments")
def add_department(
    name: str = Form(...),
    db: Session = Depends(get_db)
):
    name = name.strip()

    if not name:
        raise HTTPException(status_code=400, detail="Department name is required")

    existing = db.query(models.Department).filter(
        models.Department.name == name
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Department already exists")

    department = models.Department(name=name)
    db.add(department)
    db.commit()
    db.refresh(department)

    return {
        "message": "Department added successfully",
        "department_id": department.id,
        "name": department.name
    }


# GET DEPARTMENTS
@app.get("/departments")
def get_departments(db: Session = Depends(get_db)):
    return db.query(models.Department).order_by(
        models.Department.name.asc()
    ).all()


# DELETE DEPARTMENT
@app.delete("/departments/{department_id}")
def delete_department(
    department_id: int,
    db: Session = Depends(get_db)
):
    department = db.query(models.Department).filter(
        models.Department.id == department_id
    ).first()

    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    db.delete(department)
    db.commit()

    return {"message": "Department deleted successfully"}


# KEEP THIS AT THE VERY BOTTOM
app.mount(
    "/",
    StaticFiles(directory=FRONTEND_DIR, html=True),
    name="frontend"
)