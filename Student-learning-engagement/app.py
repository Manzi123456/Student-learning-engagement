from flask import Flask, render_template, request, redirect, url_for, flash, abort, jsonify, session, send_from_directory, send_file, Response
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect, CSRFError
from wtforms import StringField, PasswordField, BooleanField, validators, Form
from sqlalchemy import text
try:
    from flask_migrate import Migrate
except Exception:
    Migrate = None
from sqlalchemy import event
from sqlalchemy.engine import Engine
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
import os
from dotenv import load_dotenv
import smtplib
from email.message import EmailMessage
import mimetypes
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from openai import OpenAI
import re
import PyPDF2
import io
import json
import secrets
import string
import docx
import threading
import time
from datetime import timedelta
import zipfile
import xml.etree.ElementTree as ET
import difflib
import csv


# ML service import
from ml_service import train_model as ml_train_model, recommend_for_student as ml_recommend

# Load environment variables
load_dotenv()

# Ensure OPENAI_API_KEY is set, try env_file.txt as fallback
openai_api_key = os.getenv('OPENAI_API_KEY')
if not openai_api_key:
    # Try to read from env_file.txt
    try:
        with open('env_file.txt') as f:
            for line in f:
                if line.startswith('OPENAI_API_KEY='):
                    openai_api_key = line.strip().split('=', 1)[1]
                    break
    except Exception:
        pass
if not openai_api_key:
    raise RuntimeError('OPENAI_API_KEY is not set. Please set it in your environment or env_file.txt.')

# Initialize OpenAI
client = OpenAI(api_key=openai_api_key)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))

# Initialize CSRF protection
csrf = CSRFProtect()
csrf.init_app(app)

# Return JSON on CSRF errors so front-end can show a message
@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'CSRF token missing or invalid'}), 400
    flash('Your session expired. Please refresh and try again.', 'warning')
    return redirect(url_for('index'))

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///students.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Helper function to get current local datetime (already available as datetime.now())

db = SQLAlchemy(app)
if Migrate is not None:
    try:
        migrate = Migrate(app, db)
    except Exception:
        migrate = None

def calculate_engagement_score(engagement):
    """Calculate engagement score based on various metrics"""
    if not engagement:
        return 0
    
    # Base score components
    time_score = 0
    focus_score = 0
    interaction_score = 0
    scroll_score = 0
    attention_score = 0
    
    # Time spent score (0-25 points)
    total_time = engagement.total_time_spent or 0
    if total_time > 0:
        time_score = min(25, (total_time / 60) * 2)  # 2 points per minute, max 25
    
    # Focus time score (0-25 points)
    focus_time = engagement.focus_time or 0
    if total_time > 0:
        focus_ratio = focus_time / total_time
        focus_score = min(25, focus_ratio * 25)
    
    # Interaction score (0-20 points)
    clicks = engagement.clicks or 0
    cursor_moves = engagement.cursor_movements or 0
    interaction_score = min(20, (clicks * 2) + (cursor_moves / 10))
    
    # Scroll depth score (0-15 points)
    scroll_depth = engagement.scroll_depth or 0
    scroll_score = min(15, scroll_depth * 0.15)
    
    # Attention score (0-15 points) - based on distraction/return ratio
    distractions = engagement.distraction_count or 0
    returns = engagement.return_count or 0
    if distractions > 0:
        attention_ratio = returns / (distractions + returns)
        attention_score = min(15, attention_ratio * 15)
    else:
        attention_score = 15  # No distractions = full attention score
    
    # Calculate total engagement score
    total_score = time_score + focus_score + interaction_score + scroll_score + attention_score
    
    # Normalize to 0-100 range
    return min(100, max(0, int(total_score)))

def _column_names_sqlite(table_name: str):
    try:
        result = db.session.execute(text(f"PRAGMA table_info({table_name});")).mappings().all()
        return [row['name'] for row in result]
    except Exception:
        return []

def _sqlite_table_exists(table_name: str) -> bool:
    try:
        res = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"), { 't': table_name }).first()
        return res is not None
    except Exception:
        return False

def run_auto_migrations_if_needed():
    try:
        engine_name = db.engine.name
    except Exception:
        engine_name = None

    if engine_name != 'sqlite':
        return  # only perform lightweight auto-migration on SQLite

    # Question table: ensure question_type column exists and nullability of options/correct_answer is allowed
    if _sqlite_table_exists('question'):
        cols = set(_column_names_sqlite('question'))
        needs_question_migration = ('question_type' not in cols or 'marks' not in cols)
        if needs_question_migration:
            # Recreate question table with new schema
            db.session.execute(text("BEGIN TRANSACTION"))
            db.session.execute(text(
                """
                CREATE TABLE IF NOT EXISTS question_new (
                    id INTEGER PRIMARY KEY,
                    resource_id INTEGER NOT NULL,
                    question_text TEXT NOT NULL,
                    correct_answer TEXT NULL,
                    options TEXT NULL,
                    question_type VARCHAR(10) NOT NULL DEFAULT 'mcq',
                    marks INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME
                );
                """
            ))
            # Copy data from old table; default question_type to 'mcq' and marks to 1
            if {'id','resource_id','question_text','correct_answer','options','created_at'}.issubset(cols):
                db.session.execute(text(
                    """
                    INSERT INTO question_new (id, resource_id, question_text, correct_answer, options, question_type, marks, created_at)
                    SELECT id, resource_id, question_text, correct_answer, options, 'mcq' as question_type, 1 as marks, created_at FROM question;
                    """
                ))
            else:
                # Minimal fallback copy
                db.session.execute(text(
                    """
                    INSERT INTO question_new (id, resource_id, question_text, question_type, marks)
                    SELECT id, resource_id, question_text, 'mcq', 1 FROM question;
                    """
                ))
            db.session.execute(text("DROP TABLE question"))
            db.session.execute(text("ALTER TABLE question_new RENAME TO question"))
            db.session.execute(text("COMMIT"))

    # StudentAnswer: allow is_correct to be NULL and add new fields
    if _sqlite_table_exists('student_answer'):
        # Detect nullability via PRAGMA table_info
        info_rows = db.session.execute(text("PRAGMA table_info(student_answer);")).mappings().all()
        is_correct_row = next((r for r in info_rows if r.get('name') == 'is_correct'), None)
        marks_awarded_exists = any(r.get('name') == 'marks_awarded' for r in info_rows)
        teacher_feedback_exists = any(r.get('name') == 'teacher_feedback' for r in info_rows)
        graded_at_exists = any(r.get('name') == 'graded_at' for r in info_rows)
        needs_sa_migration = bool(is_correct_row and is_correct_row.get('notnull') == 1) or not marks_awarded_exists or not teacher_feedback_exists or not graded_at_exists
        if needs_sa_migration:
            db.session.execute(text("BEGIN TRANSACTION"))
            db.session.execute(text(
                """
                CREATE TABLE IF NOT EXISTS student_answer_new (
                    id INTEGER PRIMARY KEY,
                    student_id INTEGER NOT NULL,
                    question_id INTEGER NOT NULL,
                    answer TEXT NOT NULL,
                    is_correct BOOLEAN NULL,
                    marks_awarded REAL NULL,
                    teacher_feedback TEXT NULL,
                    graded_at DATETIME NULL,
                    submitted_at DATETIME,
                    plagiarism_score REAL NULL,
                    plagiarism_match_student_id INTEGER NULL,
                    plagiarism_match_answer_id INTEGER NULL,
                    plagiarism_summary TEXT NULL
                );
                """
            ))
            db.session.execute(text(
                """
                INSERT INTO student_answer_new (id, student_id, question_id, answer, is_correct, submitted_at)
                SELECT id, student_id, question_id, answer, is_correct, submitted_at FROM student_answer;
                """
            ))
            db.session.execute(text("DROP TABLE student_answer"))
            db.session.execute(text("ALTER TABLE student_answer_new RENAME TO student_answer"))
            db.session.execute(text("COMMIT"))
        else:
            # Ensure plagiarism columns exist (incremental migration)
            existing_cols = {r.get('name') for r in info_rows}
            if 'plagiarism_score' not in existing_cols:
                db.session.execute(text("ALTER TABLE student_answer ADD COLUMN plagiarism_score REAL NULL"))
            if 'plagiarism_match_student_id' not in existing_cols:
                db.session.execute(text("ALTER TABLE student_answer ADD COLUMN plagiarism_match_student_id INTEGER NULL"))
            if 'plagiarism_match_answer_id' not in existing_cols:
                db.session.execute(text("ALTER TABLE student_answer ADD COLUMN plagiarism_match_answer_id INTEGER NULL"))
            if 'plagiarism_summary' not in existing_cols:
                db.session.execute(text("ALTER TABLE student_answer ADD COLUMN plagiarism_summary TEXT NULL"))

    # QuizMetadata: add marks publishing fields
    if _sqlite_table_exists('quiz_metadata'):
        cols = set(_column_names_sqlite('quiz_metadata'))
        needs_quiz_metadata_migration = ('marks_published' not in cols or 'marks_published_at' not in cols)
        if needs_quiz_metadata_migration:
            db.session.execute(text("BEGIN TRANSACTION"))
            db.session.execute(text(
                """
                CREATE TABLE IF NOT EXISTS quiz_metadata_new (
                    id INTEGER PRIMARY KEY,
                    resource_id INTEGER NOT NULL UNIQUE,
                    time_limit INTEGER,
                    passing_score INTEGER,
                    created_by INTEGER NOT NULL,
                    created_at DATETIME,
                    marks_published BOOLEAN DEFAULT 0,
                    marks_published_at DATETIME NULL
                );
                """
            ))
            db.session.execute(text(
                """
                INSERT INTO quiz_metadata_new (id, resource_id, time_limit, passing_score, created_by, created_at)
                SELECT id, resource_id, time_limit, passing_score, created_by, created_at FROM quiz_metadata;
                """
            ))
            db.session.execute(text("DROP TABLE quiz_metadata"))
            db.session.execute(text("ALTER TABLE quiz_metadata_new RENAME TO quiz_metadata"))
            db.session.execute(text("COMMIT"))

    # StudentNotes: ensure grading columns exist (teacher_grade, teacher_feedback, graded_at, graded_by)
    if _sqlite_table_exists('student_notes'):
        existing_cols = set(_column_names_sqlite('student_notes'))
        if 'teacher_grade' not in existing_cols:
            db.session.execute(text("ALTER TABLE student_notes ADD COLUMN teacher_grade REAL NULL"))
        if 'teacher_feedback' not in existing_cols:
            db.session.execute(text("ALTER TABLE student_notes ADD COLUMN teacher_feedback TEXT NULL"))
        if 'graded_at' not in existing_cols:
            db.session.execute(text("ALTER TABLE student_notes ADD COLUMN graded_at DATETIME NULL"))
        if 'graded_by' not in existing_cols:
            db.session.execute(text("ALTER TABLE student_notes ADD COLUMN graded_by INTEGER NULL"))
        # Ensure engagement columns exist
        if 'word_count' not in existing_cols:
            db.session.execute(text("ALTER TABLE student_notes ADD COLUMN word_count INTEGER DEFAULT 0"))
        if 'character_count' not in existing_cols:
            db.session.execute(text("ALTER TABLE student_notes ADD COLUMN character_count INTEGER DEFAULT 0"))
        if 'engagement_score' not in existing_cols:
            db.session.execute(text("ALTER TABLE student_notes ADD COLUMN engagement_score REAL DEFAULT 0.0"))
    else:
        # Create table if missing (safety)
        db.session.execute(text(
            """
            CREATE TABLE IF NOT EXISTS student_notes (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL,
                resource_id INTEGER NOT NULL,
                notes_content TEXT NOT NULL,
                created_at DATETIME,
                updated_at DATETIME,
                teacher_grade REAL NULL,
                teacher_feedback TEXT NULL,
                graded_at DATETIME NULL,
                graded_by INTEGER NULL
            );
            """
        ))

# Run lightweight migrations at import time (safe for SQLite)
try:
    with app.app_context():
        run_auto_migrations_if_needed()
except Exception:
    pass
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Enforce SQLite foreign keys to avoid orphaned records
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        pass

# Email configuration
SMTP_HOST = os.getenv('SMTP_HOST')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
SMTP_USE_TLS = os.getenv('SMTP_USE_TLS', 'true').lower() in ['1', 'true', 'yes']
SMTP_USE_SSL = os.getenv('SMTP_USE_SSL', 'false').lower() in ['1', 'true', 'yes']
FROM_EMAIL = os.getenv('FROM_EMAIL', SMTP_USER or 'no-reply@example.com')

# Fallback: read SMTP settings from env_file.txt if missing
if not SMTP_HOST:
    try:
        with open('env_file.txt') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if key == 'SMTP_HOST' and not SMTP_HOST:
                    SMTP_HOST = value
                elif key == 'SMTP_PORT':
                    try:
                        SMTP_PORT = int(value)
                    except Exception:
                        pass
                elif key == 'SMTP_USER' and not SMTP_USER:
                    SMTP_USER = value
                elif key == 'SMTP_PASSWORD' and not SMTP_PASSWORD:
                    SMTP_PASSWORD = value
                elif key == 'SMTP_USE_TLS':
                    SMTP_USE_TLS = value.lower() in ['1', 'true', 'yes']
                elif key == 'SMTP_USE_SSL':
                    SMTP_USE_SSL = value.lower() in ['1', 'true', 'yes']
                elif key == 'FROM_EMAIL' and FROM_EMAIL == ('no-reply@example.com' if not SMTP_USER else SMTP_USER):
                    FROM_EMAIL = value
    except Exception:
        pass

def send_email(to_email: str, subject: str, body: str) -> bool:
    if not to_email or not SMTP_HOST:
        return False
    try:
        msg = EmailMessage()
        msg['From'] = FROM_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.set_content(body)

        if SMTP_USE_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                if SMTP_USE_TLS:
                    server.starttls()
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        return True
    except Exception:
        return False

def notify_students_of_new_resource(resource_id: int):
    try:
        resource = Resource.query.get(resource_id)
        if not resource:
            return
        students = Student.query.filter_by(teacher_id=resource.created_by, grade=resource.grade).all()
        for s in students:
            if not s.user_id:
                continue
            u = User.query.get(s.user_id)
            if not u:
                continue
            # Build different content for in-app vs email
            if resource.resource_type == 'quiz':
                app_title = f"New Quiz: {resource.title}"
                app_message = f"A new quiz has been added for Grade {resource.grade}. Click to view and start."
                subject = f"You have a new quiz: {resource.title}"
                # Include direct link to student_quiz_list where they can see available quizzes
                try:
                    quiz_link = url_for('student_quiz_list', _external=True)
                except Exception:
                    quiz_link = ''
                body = (
                    f"Hello {s.name},\n\n"
                    f"Your teacher assigned a new quiz for Grade {resource.grade}.\n"
                    f"Title: {resource.title}\n"
                    f"Description: {resource.description or 'No description'}\n\n"
                    f"Go to your quizzes: {quiz_link}\n\n"
                    f"Good luck!\n"
                    f"— Student Tracking System"
                )
            else:
                app_title = f"New Resource: {resource.title}"
                app_message = f"A new {resource.resource_type} was added. Open to study."
                subject = f"New resource available: {resource.title}"
                body = (
                    f"Hello {s.name},\n\n"
                    f"Your teacher added a new {resource.resource_type} for Grade {resource.grade}.\n"
                    f"Title: {resource.title}\n"
                    f"Description: {resource.description or 'No description'}\n\n"
                    f"Please log in to view it.\n"
                    f"Regards,\nStudent Tracking System"
                )
            # In-app notification
            try:
                notification = StudentNotification(
                    student_id=s.id,
                    resource_id=resource.id,
                    title=app_title,
                    message=app_message
                )
                db.session.add(notification)
                db.session.commit()
            except Exception:
                db.session.rollback()
            # Email notification - always attempt to send
            if getattr(u, 'email', None) and u.email.strip():
                try:
                    send_email(u.email, subject, body)
                except Exception as e:
                    print(f"Failed to send email to {u.email}: {str(e)}")
            else:
                print(f"No email address for user {u.username} (ID: {u.id})")
    except Exception:
        pass

def trigger_resource_notification_async(resource_id: int):
    t = threading.Thread(target=notify_students_of_new_resource, args=(resource_id,))
    t.daemon = True
    t.start()

@app.context_processor
def inject_notification_counts():
    student_unread = 0
    teacher_unread = 0
    try:
        if current_user.is_authenticated:
            if getattr(current_user, 'role', None) == 'student':
                student = Student.query.filter_by(user_id=current_user.id).first()
                if student:
                    student_unread = db.session.query(StudentNotification).filter_by(student_id=student.id, is_read=False).count()
            elif getattr(current_user, 'role', None) == 'teacher':
                try:
                    teacher_unread = db.session.query(TeacherNotification).filter_by(teacher_id=current_user.id, is_read=False).count()
                except Exception:
                    teacher_unread = 0
    except Exception:
        # Avoid breaking templates on DB errors
        pass
    return dict(student_unread_notifications=student_unread, teacher_unread_notifications=teacher_unread)

def ensure_user_email_column():
    try:
        from sqlalchemy import text
        info = db.session.execute(text("PRAGMA table_info('user')")).fetchall()
        columns = [row[1] for row in info]
        if 'email' not in columns:
            db.session.execute(text("ALTER TABLE user ADD COLUMN email VARCHAR(255)"))
            db.session.commit()
    except Exception:
        db.session.rollback()
        pass

def ensure_resource_soft_delete_columns():
    try:
        from sqlalchemy import text
        info = db.session.execute(text("PRAGMA table_info('resource')")).fetchall()
        columns = [row[1] for row in info]
        
        if 'is_deleted' not in columns:
            db.session.execute(text("ALTER TABLE resource ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
        if 'deleted_at' not in columns:
            db.session.execute(text("ALTER TABLE resource ADD COLUMN deleted_at DATETIME"))
        if 'deleted_by' not in columns:
            db.session.execute(text("ALTER TABLE resource ADD COLUMN deleted_by INTEGER"))
        
        db.session.commit()
    except Exception:
        db.session.rollback()
        pass

def ensure_resource_engagement_enhanced_columns():
    try:
        from sqlalchemy import text
        info = db.session.execute(text("PRAGMA table_info('resource_engagement')")).fetchall()
        columns = [row[1] for row in info]
        
        if 'reading_speed' not in columns:
            db.session.execute(text("ALTER TABLE resource_engagement ADD COLUMN reading_speed REAL DEFAULT 0.0"))
        if 'comprehension_score' not in columns:
            db.session.execute(text("ALTER TABLE resource_engagement ADD COLUMN comprehension_score REAL DEFAULT 0.0"))
        if 'engagement_score' not in columns:
            db.session.execute(text("ALTER TABLE resource_engagement ADD COLUMN engagement_score REAL DEFAULT 0.0"))
        if 'attention_span' not in columns:
            db.session.execute(text("ALTER TABLE resource_engagement ADD COLUMN attention_span INTEGER DEFAULT 0"))
        if 'distraction_count' not in columns:
            db.session.execute(text("ALTER TABLE resource_engagement ADD COLUMN distraction_count INTEGER DEFAULT 0"))
        if 'return_count' not in columns:
            db.session.execute(text("ALTER TABLE resource_engagement ADD COLUMN return_count INTEGER DEFAULT 0"))
        
        db.session.commit()
    except Exception:
        db.session.rollback()
        pass

with app.app_context():
    ensure_user_email_column()
    ensure_resource_soft_delete_columns()
    ensure_resource_engagement_enhanced_columns()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'admin', 'teacher' or 'student'
    email = db.Column(db.String(255), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# Centralized list of allowed roles for admin management
ALLOWED_ROLES = ['admin', 'teacher', 'student', 'staff', 'moderator']

class PasswordResetToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token = db.Column(db.String(128), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)

def ensure_password_reset_table():
    try:
        info = db.session.execute(text("PRAGMA table_info('password_reset_token')")).fetchall()
        if not info:
            db.session.execute(text(
                """
                CREATE TABLE IF NOT EXISTS password_reset_token (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    token VARCHAR(128) NOT NULL UNIQUE,
                    created_at DATETIME,
                    expires_at DATETIME NOT NULL,
                    used_at DATETIME NULL,
                    FOREIGN KEY(user_id) REFERENCES user(id)
                )
                """
            ))
            db.session.commit()
    except Exception:
        db.session.rollback()
        pass

# Ensure password reset table after defining the helper
with app.app_context():
    ensure_password_reset_table()

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    student_id = db.Column(db.String(20), unique=True, nullable=False)
    grade = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # Teacher who created this student (nullable for self-registered)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<Student {self.name}>'

class Resource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    resource_type = db.Column(db.String(20), nullable=False)  # 'note', 'video', 'link'
    file_path = db.Column(db.String(255))
    url = db.Column(db.String(500))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    grade = db.Column(db.String(10), nullable=False)
    is_deleted = db.Column(db.Boolean, default=False)  # Soft delete flag
    deleted_at = db.Column(db.DateTime)  # When it was deleted
    deleted_by = db.Column(db.Integer, db.ForeignKey('user.id'))  # Who deleted it
    access_time_limit = db.Column(db.Integer, default=0, nullable=True)  # Time limit in minutes (0 = no limit)

    def __repr__(self):
        return f'<Resource {self.title}>'

class ResourceAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    assigned_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Teacher who assigned
    assigned_at = db.Column(db.DateTime, default=datetime.now)
    access_key = db.Column(db.String(20), unique=True, nullable=True)  # Unique access key (can be null for direct assignment)
    max_students = db.Column(db.Integer, default=1)  # Maximum number of students who can access
    is_active = db.Column(db.Boolean, default=True)  # Whether the assignment is active
    
    # Ensure unique assignment
    __table_args__ = (db.UniqueConstraint('resource_id', 'student_id', name='unique_resource_student'),)

    def __repr__(self):
        return f'<ResourceAssignment {self.resource_id}-{self.student_id}>'

class ResourceAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    access_key = db.Column(db.String(20), unique=True, nullable=False)
    max_students = db.Column(db.Integer, nullable=False, default=30)
    current_usage = db.Column(db.Integer, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f'<ResourceAccess {self.access_key}>'

class QuizReassessment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    granted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    granted_at = db.Column(db.DateTime, default=datetime.now)
    is_used = db.Column(db.Boolean, default=False)
    used_at = db.Column(db.DateTime)
    reason = db.Column(db.Text)

    def __repr__(self):
        return f'<QuizReassessment {self.student_id}-{self.resource_id}>'

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    # For MCQ, correct_answer stores the letter (A/B/C/D) or the correct option text (legacy)
    # For essay questions, correct_answer can be null/empty; grading is manual
    correct_answer = db.Column(db.Text, nullable=True)
    # List of answer options for MCQ; null for essay questions
    options = db.Column(db.JSON, nullable=True)
    # 'mcq' or 'essay' (default to 'mcq' for backward compatibility)
    question_type = db.Column(db.String(10), nullable=False, default='mcq')
    # Marks allocated to this question
    marks = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<Question {self.id}>'

class StudentAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    # Nullable to support essay/manual grading
    is_correct = db.Column(db.Boolean, nullable=True)
    # Marks awarded for this answer (for manual grading)
    marks_awarded = db.Column(db.Float, nullable=True)
    # Teacher's feedback for essay questions
    teacher_feedback = db.Column(db.Text, nullable=True)
    # When the answer was graded by teacher
    graded_at = db.Column(db.DateTime, nullable=True)
    submitted_at = db.Column(db.DateTime, default=datetime.now)
    # Plagiarism/copy detection fields (essay questions)
    plagiarism_score = db.Column(db.Float, nullable=True)  # 0.0 - 1.0 similarity
    plagiarism_match_student_id = db.Column(db.Integer, nullable=True)
    plagiarism_match_answer_id = db.Column(db.Integer, nullable=True)
    plagiarism_summary = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<StudentAnswer {self.id}>'

class StudySession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime)
    duration = db.Column(db.Integer)  # Duration in seconds
    quiz_score = db.Column(db.Float)  # Percentage score on the quiz
    completed = db.Column(db.Boolean, default=False)
    ai_recommendation = db.Column(db.Text)  # Store AI-generated recommendations

    def __repr__(self):
        return f'<StudySession {self.id}>'

class StudentActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('study_session.id'), nullable=True)
    activity_type = db.Column(db.String(50), nullable=False)  # 'page_view', 'scroll', 'cursor_move', 'click', 'focus', 'blur'
    timestamp = db.Column(db.DateTime, default=datetime.now, nullable=False)
    data = db.Column(db.JSON)  # Store activity-specific data (coordinates, scroll position, etc.)
    
    def __repr__(self):
        return f'<StudentActivity {self.id}>'

@app.route('/api/track_activity', methods=['POST'])
@login_required
def track_activity_api():
    """Record fine-grained student activity and roll up engagement metrics."""
    if current_user.role != 'student':
        return jsonify({'success': False, 'error': 'Only students can track activity'}), 403
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return jsonify({'success': False, 'error': 'Student profile not found'}), 404

    try:
        payload = request.get_json(force=True)
        resource_id = int(payload.get('resource_id')) if payload and payload.get('resource_id') is not None else None
        session_id = payload.get('session_id')
        activity_type = (payload.get('activity_type') or '').strip()
        data = payload.get('data') or {}
        if not resource_id or not activity_type:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        # Ensure resource exists and student can access it (same teacher and grade or assigned)
        resource = Resource.query.get_or_404(resource_id)
        if not (resource.created_by == student.teacher_id and resource.grade == student.grade) and not ResourceAssignment.query.filter_by(resource_id=resource_id, student_id=student.id).first():
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        # Resolve study session context if provided or find latest active
        session_obj = None
        if session_id:
            try:
                session_obj = StudySession.query.get(int(session_id))
            except Exception:
                session_obj = None
        if not session_obj:
            session_obj = StudySession.query.filter_by(student_id=student.id, resource_id=resource_id, completed=False).order_by(StudySession.start_time.desc()).first()

        # Persist raw activity
        activity = StudentActivity(
            student_id=student.id,
            resource_id=resource_id,
            session_id=session_obj.id if session_obj else None,
            activity_type=activity_type,
            data=data
        )
        db.session.add(activity)

        # Update or create engagement rollup
        engagement = ResourceEngagement.query.filter_by(student_id=student.id, resource_id=resource_id, session_id=(session_obj.id if session_obj else None)).first()
        if not engagement:
            engagement = ResourceEngagement(
                student_id=student.id,
                resource_id=resource_id,
                session_id=session_obj.id if session_obj else None,
                total_time_spent=0,
                scroll_depth=0.0,
                cursor_movements=0,
                clicks=0,
                focus_time=0,
                idle_time=0,
                last_updated=datetime.now(),
            )
            db.session.add(engagement)

        # Roll up metrics based on activity type
        try:
            if activity_type == 'time_spent':
                engagement.total_time_spent = (engagement.total_time_spent or 0) + int(data.get('duration', 0) or 0)
            elif activity_type == 'scroll':
                engagement.scroll_depth = max(engagement.scroll_depth or 0.0, float(data.get('max_scroll_depth') or data.get('scroll_percentage') or 0.0))
            elif activity_type == 'cursor_move':
                engagement.cursor_movements = (engagement.cursor_movements or 0) + 1
            elif activity_type == 'click':
                engagement.clicks = (engagement.clicks or 0) + 1
            elif activity_type == 'focus_time':
                engagement.focus_time = (engagement.focus_time or 0) + int(data.get('duration', 0) or 0)
            elif activity_type == 'idle_time':
                engagement.idle_time = (engagement.idle_time or 0) + int(data.get('duration', 0) or 0)
            elif activity_type == 'session_end':
                # Align rollup with final payload on session end
                engagement.total_time_spent = int(data.get('total_time_spent', engagement.total_time_spent or 0))
                engagement.scroll_depth = float(data.get('max_scroll_depth', engagement.scroll_depth or 0.0))
                engagement.cursor_movements = int(data.get('total_cursor_movements', engagement.cursor_movements or 0))
                engagement.clicks = int(data.get('total_clicks', engagement.clicks or 0))
                engagement.focus_time = int(data.get('total_focus_time', engagement.focus_time or 0))
                engagement.idle_time = int(data.get('total_idle_time', engagement.idle_time or 0))
        except Exception:
            # Do not block on bad client payloads
            pass

        engagement.last_updated = datetime.now()
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

class ResourceEngagement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('study_session.id'), nullable=True)
    total_time_spent = db.Column(db.Integer, default=0)  # Total seconds spent on resource
    scroll_depth = db.Column(db.Float, default=0.0)  # Maximum scroll percentage reached
    cursor_movements = db.Column(db.Integer, default=0)  # Number of cursor movements
    clicks = db.Column(db.Integer, default=0)  # Number of clicks
    focus_time = db.Column(db.Integer, default=0)  # Time with page in focus (seconds)
    idle_time = db.Column(db.Integer, default=0)  # Time inactive (seconds)
    last_updated = db.Column(db.DateTime, default=datetime.now)
    
    # Enhanced tracking metrics for ML
    reading_speed = db.Column(db.Float, default=0.0)  # Words per minute
    comprehension_score = db.Column(db.Float, default=0.0)  # Estimated comprehension (0-100)
    engagement_score = db.Column(db.Float, default=0.0)  # Overall engagement score (0-100)
    attention_span = db.Column(db.Integer, default=0)  # Average attention span in seconds
    distraction_count = db.Column(db.Integer, default=0)  # Number of times student left page
    return_count = db.Column(db.Integer, default=0)  # Number of times student returned
    
    # Unique constraint to prevent duplicate engagement records
    __table_args__ = (db.UniqueConstraint('student_id', 'resource_id', 'session_id', name='unique_engagement'),)
    
    def __repr__(self):
        return f'<ResourceEngagement {self.student_id}-{self.resource_id}>'

# Secure inline serving of resources to discourage direct downloads
@app.route('/resource/<int:resource_id>/inline')
@login_required
def serve_resource_inline(resource_id):
    # Only students or teachers can access
    resource = Resource.query.get_or_404(resource_id)
    # Determine path under static/uploads or original path
    file_path = resource.file_path or ''
    safe_name = file_path.split('/')[-1].split('\\')[-1]
    
    # Handle HTML files specially for inline viewing
    if safe_name.lower().endswith('.html'):
        # Read and return HTML content directly for inline viewing
        full_path = os.path.join('static', 'uploads', safe_name)
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            return html_content, 200, {'Content-Type': 'text/html; charset=utf-8'}
        except Exception as e:
            abort(500)
    
    # Handle video files with proper MIME types
    video_extensions = {
        '.mp4': 'video/mp4',
        '.webm': 'video/webm',
        '.ogg': 'video/ogg',
        '.avi': 'video/x-msvideo',
        '.mov': 'video/quicktime',
        '.wmv': 'video/x-ms-wmv',
        '.flv': 'video/x-flv',
        '.mkv': 'video/x-matroska'
    }
    
    # Get file extension
    file_ext = os.path.splitext(safe_name.lower())[1]
    
    # Set proper MIME type for videos
    if file_ext in video_extensions:
        mimetype = video_extensions[file_ext]
    else:
        # Use mimetypes for other files
        mimetype = mimetypes.guess_type(safe_name)[0] or 'application/octet-stream'
    
    # Check if file exists
    file_path = os.path.join('static', 'uploads', safe_name)
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        abort(404, description=f"File {safe_name} not found")
    
    # Always serve from static/uploads to avoid exposing real FS
    return send_from_directory(
        os.path.join('static', 'uploads'), 
        safe_name, 
        as_attachment=False, 
        mimetype=mimetype
    )

# Tracked external link opener
@app.route('/resource/<int:resource_id>/open')
@login_required
def open_tracked_link(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    if not resource.url:
        abort(404)
    # Record click for tracking
    if current_user.role == 'student':
        student = Student.query.filter_by(user_id=current_user.id).first()
        if student:
            activity = StudentActivity(
                student_id=student.id,
                resource_id=resource.id,
                activity_type='click',
                data={'element': 'A', 'element_id': 'tracked-link', 'element_class': 'tracked-link'}
            )
            db.session.add(activity)
            db.session.commit()
    # Redirect out
    return redirect(resource.url)

 

@app.route('/resource/<int:resource_id>/download')
@login_required
def download_tracked(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    if not resource.file_path:
        abort(404)
    # Track download event for students
    if current_user.role == 'student':
        student = Student.query.filter_by(user_id=current_user.id).first()
        if student:
            activity = StudentActivity(
                student_id=student.id,
                resource_id=resource.id,
                activity_type='download',
                data={'filename': resource.file_path.split('/')[-1]}
            )
            db.session.add(activity)
            db.session.commit()
    safe_name = resource.file_path.split('/')[-1].split('\\')[-1]
    return send_from_directory(os.path.join('static','uploads'), safe_name, as_attachment=True, download_name=safe_name)

class QuizMetadata(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), unique=True, nullable=False)
    time_limit = db.Column(db.Integer) # in seconds
    passing_score = db.Column(db.Integer) # in percentage
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    # Whether marks have been published to students
    marks_published = db.Column(db.Boolean, default=False)
    # When marks were published
    marks_published_at = db.Column(db.DateTime, nullable=True)

class StudentNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }
    
    def __repr__(self):
        return f"<StudentNote {self.id} - {self.title}>"

class StudentNotes(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    notes_content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    word_count = db.Column(db.Integer, default=0)
    character_count = db.Column(db.Integer, default=0)
    engagement_score = db.Column(db.Float, default=0.0)
    teacher_grade = db.Column(db.Float, nullable=True)
    teacher_feedback = db.Column(db.Text, nullable=True)
    graded_at = db.Column(db.DateTime, nullable=True)
    graded_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    __table_args__ = (db.UniqueConstraint('student_id', 'resource_id', name='unique_student_resource_notes'),)
    
    def __repr__(self):
        return f"<StudentNotes {self.id} - Student: {self.student_id}, Resource: {self.resource_id}>"

class StudentSuccessPrediction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('study_session.id'), nullable=True)
    predicted_score = db.Column(db.Float, nullable=False)  # Predicted quiz score (0-100)
    success_probability = db.Column(db.Float, nullable=False)  # Probability of success (0-1)
    confidence_level = db.Column(db.Float, nullable=False)  # Model confidence (0-1)
    prediction_factors = db.Column(db.JSON)  # Factors that influenced prediction
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    def __repr__(self):
        return f'<StudentSuccessPrediction {self.student_id}-{self.resource_id}>'

class TeacherNotification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=True)
    notification_type = db.Column(db.String(50), nullable=False)  # 'low_engagement', 'success_prediction', 'completed_quiz', 'idle_alert'
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), default='info')  # 'info', 'warning', 'alert'
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    def __repr__(self):
        return f'<TeacherNotification {self.id}>'

class StudentNotification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<StudentNotification {self.id}>'


def _notify_teacher_quiz_submission(student: 'Student', resource_id: int) -> None:
    """Create a throttled teacher notification that a student submitted quiz work.
    Avoid spamming by sending at most one per 10 minutes per quiz.
    """
    try:
        recent = TeacherNotification.query.filter_by(
            teacher_id=student.teacher_id,
            resource_id=resource_id,
            notification_type='quiz_submission'
        ).order_by(TeacherNotification.created_at.desc()).first()
        allow = True
        if recent and (datetime.now() - recent.created_at) < timedelta(minutes=10):
            allow = False
        if allow:
            tn = TeacherNotification(
                teacher_id=student.teacher_id,
                student_id=student.id,
                resource_id=resource_id,
                notification_type='quiz_submission',
                title='New Quiz Activity',
                message=f"{student.name} submitted an answer.",
                severity='info',
                is_read=False
            )
            db.session.add(tn)
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"notify submission failed: {e}")

class StudentLearningProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), unique=True, nullable=False)
    learning_style = db.Column(db.String(50))  # 'visual', 'auditory', 'kinesthetic', 'reading'
    attention_span_avg = db.Column(db.Integer)  # Average attention span in minutes
    preferred_session_duration = db.Column(db.Integer)  # Preferred study session length in minutes
    engagement_pattern = db.Column(db.JSON)  # Pattern of engagement over time
    success_factors = db.Column(db.JSON)  # Factors that contribute to success
    last_updated = db.Column(db.DateTime, default=datetime.now)
    
    def __repr__(self):
        return f'<StudentLearningProfile {self.student_id}>'

# Bootstrap initial admin after models are defined
with app.app_context():
    # Ensure tables exist before any queries and enable SQLite FKs
    try:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        @event.listens_for(Engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            try:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
            except Exception:
                pass

        db.create_all()
    except Exception:
        pass

    # Bootstrap initial admin if configured and missing
    initial_admin_username = os.getenv('INITIAL_ADMIN_USERNAME')
    initial_admin_email = os.getenv('INITIAL_ADMIN_EMAIL')
    initial_admin_password = os.getenv('INITIAL_ADMIN_PASSWORD')

    # Fallback: read from env_file.txt if not set in environment
    if not (initial_admin_username and initial_admin_email and initial_admin_password):
        try:
            with open('env_file.txt') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('INITIAL_ADMIN_USERNAME=') and not initial_admin_username:
                        initial_admin_username = line.split('=', 1)[1]
                    elif line.startswith('INITIAL_ADMIN_EMAIL=') and not initial_admin_email:
                        initial_admin_email = line.split('=', 1)[1]
                    elif line.startswith('INITIAL_ADMIN_PASSWORD=') and not initial_admin_password:
                        initial_admin_password = line.split('=', 1)[1]
        except Exception:
            pass

    if initial_admin_username and initial_admin_email and initial_admin_password:
        try:
            existing_admin = User.query.filter_by(username=initial_admin_username).first()
            if not existing_admin:
                admin_user = User(username=initial_admin_username, email=initial_admin_email, role='admin')
                admin_user.set_password(initial_admin_password)
                db.session.add(admin_user)
                db.session.commit()
        except Exception:
            db.session.rollback()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def teacher_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'teacher':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

def student_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'student':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        elif current_user.role == 'teacher':
            return redirect(url_for('teacher_dashboard'))
        elif current_user.role == 'student':
            # Check if student record exists
            student = Student.query.filter_by(user_id=current_user.id).first()
            if student:
                return redirect(url_for('student_dashboard'))
            else:
                # Student account exists but no student record
                flash('Your student account is not properly set up. Please contact your teacher.', 'warning')
                logout_user()
                return redirect(url_for('login'))
        else:
            # Invalid role
            flash('Invalid account type. Please contact administrator.', 'danger')
            logout_user()
            return redirect(url_for('login'))
    return redirect(url_for('login'))

@app.route('/create_account', methods=['POST'])
def create_account():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')
    role = request.form.get('role')
    full_name = request.form.get('full_name')
    student_id = request.form.get('student_id')
    grade = request.form.get('grade')
    
    # Validation
    if not all([username, email, password, confirm_password, role]):
        flash('All required fields must be filled.', 'danger')
        return redirect(url_for('login'))
    
    if password != confirm_password:
        flash('Passwords do not match.', 'danger')
        return redirect(url_for('login'))
    
    if len(password) < 6:
        flash('Password must be at least 6 characters long.', 'danger')
        return redirect(url_for('login'))
    
    if User.query.filter_by(username=username).first():
        flash('Username already exists. Please choose a different username.', 'danger')
        return redirect(url_for('login'))
    if email and db.session.query(User).filter(db.func.lower(User.email) == (email.lower())).first():
        flash('Email already in use. Please use a different email.', 'danger')
        return redirect(url_for('login'))
    
    if role == 'admin':
        flash('Admin accounts cannot be created via public registration.', 'danger')
        return redirect(url_for('login'))

    try:
        # Create user account
        user = User(username=username, role=role, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        
        # If creating a student account, create student record
        if role == 'student':
            if not all([full_name, student_id, grade]):
                flash('Student information is incomplete.', 'danger')
                return redirect(url_for('login'))
            
            if Student.query.filter_by(student_id=student_id).first():
                flash('Student ID already exists.', 'danger')
                return redirect(url_for('login'))
            
            student = Student(
                name=full_name,
                student_id=student_id,
                grade=grade,
                user_id=user.id,
                teacher_id=None  # Student self-registration, no teacher assigned
            )
            db.session.add(student)
        
        db.session.commit()
        flash('Account created successfully! You can now login.', 'success')
        return redirect(url_for('login'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating account: {str(e)}', 'danger')
        return redirect(url_for('login'))

class WipeForm(FlaskForm):
    confirm_token = StringField('Confirm Token', [
        validators.InputRequired(),
        validators.Length(min=4, max=4)
    ])

class LoginForm(FlaskForm):
    username = StringField('Username', [
        validators.InputRequired(),
        validators.Length(min=4, max=25)
    ])
    password = PasswordField('Password', [
        validators.InputRequired(),
        validators.Length(min=6)
    ])
    remember = BooleanField('Remember Me')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    form = LoginForm()
    
    if form.validate_on_submit():
        username = form.username.data.strip()
        password = form.password.data
        remember = form.remember.data
        
        # Case-insensitive username lookup
        user = db.session.query(User).filter(db.func.lower(User.username) == username.lower()).first()
        
        if user and user.check_password(password):
            login_user(user, remember=remember)
            flash('Logged in successfully!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash('Invalid username or password.', 'danger')
    
    return render_template('login.html', form=form)

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        identifier = (request.form.get('identifier') or '').strip()
        if not identifier:
            flash('Please enter your username or email.', 'danger')
            return redirect(url_for('forgot_password'))
        # Lookup by username first, then email
        user = db.session.query(User).filter(db.func.lower(User.username) == identifier.lower()).first()
        if not user:
            user = db.session.query(User).filter(db.func.lower(User.email) == identifier.lower()).first()
        if not user:
            flash('If the account exists, a reset email has been sent.', 'info')
            return redirect(url_for('login'))
        # Create token
        token = secrets.token_urlsafe(48)
        expires_at = datetime.now() + timedelta(hours=1)
        prt = PasswordResetToken(user_id=user.id, token=token, expires_at=expires_at)
        db.session.add(prt)
        db.session.commit()
        # Send email
        reset_link = url_for('reset_password', token=token, _external=True)
        subject = 'Password Reset Request'
        body = f"Hello {user.username},\n\nYou requested a password reset. Click the link below to set a new password.\n\n{reset_link}\n\nThis link expires in 1 hour. If you did not request this, you can ignore this email.\n\nRegards,\nStudent Tracking System"
        try:
            send_email(user.email, subject, body)
        except Exception:
            pass
        flash('If the account exists, a reset email has been sent.', 'info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    prt = PasswordResetToken.query.filter_by(token=token).first()
    if not prt or prt.used_at is not None or prt.expires_at < datetime.now():
        flash('Invalid or expired reset link.', 'danger')
        return redirect(url_for('login'))
    user = User.query.get(prt.user_id)
    if not user:
        flash('Invalid reset token.', 'danger')
        return redirect(url_for('login'))
    if request.method == 'POST':
        password = (request.form.get('password') or '').strip()
        confirm = (request.form.get('confirm_password') or '').strip()
        if not password or not confirm:
            flash('Please enter and confirm your new password.', 'danger')
            return redirect(url_for('reset_password', token=token))
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('reset_password', token=token))
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return redirect(url_for('reset_password', token=token))
        # Update password and mark token used
        user.set_password(password)
        prt.used_at = datetime.now()
        db.session.add(user)
        db.session.add(prt)
        db.session.commit()
        flash('Your password has been reset. You can now log in.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token, email=user.email)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    student_record = None
    if current_user.role == 'student':
        student_record = Student.query.filter_by(user_id=current_user.id).first()

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip()
        current_password = (request.form.get('current_password') or '').strip()
        new_password = (request.form.get('new_password') or '').strip()
        confirm_password = (request.form.get('confirm_password') or '').strip()
        if not email:
            flash('Email is required.', 'danger')
            return redirect(url_for('edit_profile'))

        # Collect student-specific fields when applicable
        if current_user.role == 'student':
            name = (request.form.get('name') or '').strip()
            sid = (request.form.get('student_id') or '').strip()
            grade = (request.form.get('grade') or '').strip()
            if not all([name, sid, grade]):
                flash('Name, Student ID and Grade are required.', 'danger')
                return redirect(url_for('edit_profile'))

        try:
            # Check for email uniqueness (case-insensitive) except current user
            existing = db.session.query(User).filter(db.func.lower(User.email) == email.lower(), User.id != current_user.id).first()
            if existing:
                flash('Email already in use.', 'danger')
                return redirect(url_for('edit_profile'))

            # If username provided, check uniqueness and update
            if username and username != current_user.username:
                existing_username = db.session.query(User).filter(db.func.lower(User.username) == username.lower(), User.id != current_user.id).first()
                if existing_username:
                    flash('Username already taken.', 'danger')
                    return redirect(url_for('edit_profile'))
                current_user.username = username

            current_user.email = email

            # Handle password change if any password fields provided
            if any([current_password, new_password, confirm_password]):
                if not all([current_password, new_password, confirm_password]):
                    flash('To change password, fill all password fields.', 'danger')
                    return redirect(url_for('edit_profile'))
                if not current_user.check_password(current_password):
                    flash('Current password is incorrect.', 'danger')
                    return redirect(url_for('edit_profile'))
                if new_password != confirm_password:
                    flash('New passwords do not match.', 'danger')
                    return redirect(url_for('edit_profile'))
                if len(new_password) < 6:
                    flash('New password must be at least 6 characters.', 'danger')
                    return redirect(url_for('edit_profile'))
                current_user.set_password(new_password)

            # Update linked student fields
            if current_user.role == 'student' and student_record:
                # Ensure student_id uniqueness among students
                existing_student = Student.query.filter_by(student_id=sid).first()
                if existing_student and existing_student.id != student_record.id:
                    flash('Student ID already exists.', 'danger')
                    return redirect(url_for('edit_profile'))
                student_record.name = name
                student_record.student_id = sid
                student_record.grade = grade

            db.session.commit()
            flash('Profile updated.', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            return redirect(url_for('edit_profile'))

    return render_template('edit_profile.html', student=student_record)


# -------------------- Admin routes --------------------
@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    users = User.query.order_by(User.username.asc()).all()
    return render_template('admin_dashboard.html', users=users, allowed_roles=ALLOWED_ROLES)

@app.route('/admin/wipe', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_wipe_accounts():
    form = WipeForm()
    if form.validate_on_submit():
        token = form.confirm_token.data.strip().lower()
        if token != 'wipe':
            flash("Type 'wipe' to confirm.", 'danger')
            return render_template('admin_wipe_accounts.html', form=form)
        try:
            # Compute IDs to wipe
            student_ids = [s.id for s in Student.query.all()]
            teacher_ids = [u.id for u in User.query.filter_by(role='teacher').all()]
            # Resources created by teachers (non-admins)
            resource_ids = [r.id for r in Resource.query.filter(Resource.created_by.in_(teacher_ids)).all()]

            # Dependent deletions (order matters)
            if student_ids:
                StudentNotification.query.filter(StudentNotification.student_id.in_(student_ids)).delete(synchronize_session=False)
                StudentLearningProfile.query.filter(StudentLearningProfile.student_id.in_(student_ids)).delete(synchronize_session=False)
                StudentSuccessPrediction.query.filter(StudentSuccessPrediction.student_id.in_(student_ids)).delete(synchronize_session=False)
                # Collect session IDs for these students and delete all session-linked rows first
                _sess_rows = db.session.query(StudySession.id).filter(StudySession.student_id.in_(student_ids)).all()
                session_ids = [row[0] for row in _sess_rows]
                if session_ids:
                    StudentSuccessPrediction.query.filter(StudentSuccessPrediction.session_id.in_(session_ids)).delete(synchronize_session=False)
                    ResourceEngagement.query.filter(ResourceEngagement.session_id.in_(session_ids)).delete(synchronize_session=False)
                    StudentActivity.query.filter(StudentActivity.session_id.in_(session_ids)).delete(synchronize_session=False)
                # Also remove direct per-student dependencies before deleting sessions
                ResourceEngagement.query.filter(ResourceEngagement.student_id.in_(student_ids)).delete(synchronize_session=False)
                StudentActivity.query.filter(StudentActivity.student_id.in_(student_ids)).delete(synchronize_session=False)
                # Answers submitted by these students
                StudentAnswer.query.filter(StudentAnswer.student_id.in_(student_ids)).delete(synchronize_session=False)
                # Ensure above deletes are flushed before removing sessions (SQLite ordering)
                db.session.flush()
                # Delete sessions after removing all dependents
                StudySession.query.filter(StudySession.student_id.in_(student_ids)).delete(synchronize_session=False)
                QuizReassessment.query.filter(QuizReassessment.student_id.in_(student_ids)).delete(synchronize_session=False)
                ResourceAssignment.query.filter(ResourceAssignment.student_id.in_(student_ids)).delete(synchronize_session=False)

            if teacher_ids:
                TeacherNotification.query.filter(TeacherNotification.teacher_id.in_(teacher_ids)).delete(synchronize_session=False)
                ResourceAccess.query.filter(ResourceAccess.created_by.in_(teacher_ids)).delete(synchronize_session=False)

            if resource_ids:
                # Delete objects tied to resources created by teachers
                QuizMetadata.query.filter(QuizMetadata.resource_id.in_(resource_ids)).delete(synchronize_session=False)
                # Delete answers BEFORE deleting questions to satisfy FK constraints
                StudentAnswer.query.filter(
                    StudentAnswer.question_id.in_(
                        db.session.query(Question.id).filter(Question.resource_id.in_(resource_ids))
                    )
                ).delete(synchronize_session=False)
                # Delete session-linked analytics for these resources via materialized session_ids
                _res_sess_rows = db.session.query(StudySession.id).filter(StudySession.resource_id.in_(resource_ids)).all()
                res_session_ids = [row[0] for row in _res_sess_rows]
                if res_session_ids:
                    ResourceEngagement.query.filter(ResourceEngagement.session_id.in_(res_session_ids)).delete(synchronize_session=False)
                    StudentActivity.query.filter(StudentActivity.session_id.in_(res_session_ids)).delete(synchronize_session=False)
                    StudentSuccessPrediction.query.filter(StudentSuccessPrediction.session_id.in_(res_session_ids)).delete(synchronize_session=False)
                Question.query.filter(Question.resource_id.in_(resource_ids)).delete(synchronize_session=False)
                StudySession.query.filter(StudySession.resource_id.in_(resource_ids)).delete(synchronize_session=False)
                ResourceEngagement.query.filter(ResourceEngagement.resource_id.in_(resource_ids)).delete(synchronize_session=False)
                StudentActivity.query.filter(StudentActivity.resource_id.in_(resource_ids)).delete(synchronize_session=False)
                ResourceAssignment.query.filter(ResourceAssignment.resource_id.in_(resource_ids)).delete(synchronize_session=False)
                StudentNotification.query.filter(StudentNotification.resource_id.in_(resource_ids)).delete(synchronize_session=False)
                TeacherNotification.query.filter(TeacherNotification.resource_id.in_(resource_ids)).delete(synchronize_session=False)
                StudentSuccessPrediction.query.filter(StudentSuccessPrediction.resource_id.in_(resource_ids)).delete(synchronize_session=False)
                QuizReassessment.query.filter(QuizReassessment.resource_id.in_(resource_ids)).delete(synchronize_session=False)
                Resource.query.filter(Resource.id.in_(resource_ids)).delete(synchronize_session=False)

            # Finally delete students and all non-admin users
            if student_ids:
                Student.query.filter(Student.id.in_(student_ids)).delete(synchronize_session=False)
            User.query.filter(User.role != 'admin').delete(synchronize_session=False)

            db.session.commit()
            flash('All non-admin accounts and related data have been deleted.', 'success')
            return redirect(url_for('admin_dashboard'))
        except Exception as e:
            db.session.rollback()
            app.logger.exception('Error wiping accounts')
            flash(f'Error wiping accounts: {str(e)}', 'danger')
    return render_template('admin_wipe_accounts.html', form=form)

@app.route('/admin/register', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_register_user():
    if request.method == 'POST':
        # Verify CSRF token
        if not request.form.get('csrf_token') or request.form.get('csrf_token') != csrf._get_csrf_token():
            abort(400, 'Invalid CSRF token')
            
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role')

        # Optional student fields
        name = request.form.get('name')
        student_id = request.form.get('student_id')
        grade = request.form.get('grade')
        teacher_id = request.form.get('teacher_id')

        if not all([username, email, password, role]):
            flash('All required fields must be filled.', 'danger')
            return redirect(url_for('admin_register_user'))

        if role not in ALLOWED_ROLES:
            flash('Invalid role selected.', 'danger')
            return redirect(url_for('admin_register_user'))

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('admin_register_user'))
        if email and db.session.query(User).filter(db.func.lower(User.email) == (email.lower())).first():
            flash('Email already in use.', 'danger')
            return redirect(url_for('admin_register_user'))

        try:
            user = User(username=username, role=role, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()

            if role == 'student':
                if not all([name, student_id, grade, teacher_id]):
                    flash('Student information is required for student role.', 'danger')
                    return redirect(url_for('admin_register_user'))

                if Student.query.filter_by(student_id=student_id).first():
                    flash('Student ID already exists.', 'danger')
                    return redirect(url_for('admin_register_user'))

                student = Student(
                    name=name,
                    student_id=student_id,
                    grade=grade,
                    user_id=user.id,
                    teacher_id=int(teacher_id)
                )
                db.session.add(student)

            db.session.commit()
            flash('User created successfully.', 'success')
            return redirect(url_for('admin_dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating user: {str(e)}', 'danger')
            return redirect(url_for('admin_register_user'))
    # GET: load teachers for assignment and optionally preselect role
    teachers = User.query.filter_by(role='teacher').order_by(User.username.asc()).all()
    selected_role = (request.args.get('role') or '').strip() or None
    if selected_role not in ALLOWED_ROLES:
        selected_role = None
    return render_template('admin_register_user.html', teachers=teachers, selected_role=selected_role)

@app.route('/admin/users/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(id):
    user = User.query.get_or_404(id)
    # Prevent deleting self to avoid being locked out
    if user.id == current_user.id:
        flash('You cannot delete your own account while logged in.', 'warning')
        return redirect(url_for('admin_dashboard'))

    # Prevent deleting the last admin
    if user.role == 'admin':
        admin_count = User.query.filter_by(role='admin').count()
        if admin_count <= 1:
            flash('Cannot delete the last admin user.', 'danger')
            return redirect(url_for('admin_dashboard'))

    try:
        # If deleting student, also clean up their Student record
        if user.role == 'student':
            student = Student.query.filter_by(user_id=user.id).first()
            if student:
                # Get session IDs first before deleting sessions
                session_ids = db.session.query(StudySession.id).filter_by(student_id=student.id).all()
                session_ids = [row[0] for row in session_ids]
                
                # Delete dependent records first (in correct order)
                if session_ids:
                    # Delete records that reference study_session.id
                    StudentActivity.query.filter(StudentActivity.session_id.in_(session_ids)).delete(synchronize_session=False)
                    ResourceEngagement.query.filter(ResourceEngagement.session_id.in_(session_ids)).delete(synchronize_session=False)
                
                # Delete other dependent records
                StudentNotification.query.filter_by(student_id=student.id).delete(synchronize_session=False)
                TeacherNotification.query.filter_by(student_id=student.id).delete(synchronize_session=False)
                StudentLearningProfile.query.filter_by(student_id=student.id).delete(synchronize_session=False)
                StudentSuccessPrediction.query.filter_by(student_id=student.id).delete(synchronize_session=False)
                ResourceEngagement.query.filter_by(student_id=student.id).delete(synchronize_session=False)
                StudentActivity.query.filter_by(student_id=student.id).delete(synchronize_session=False)
                StudentAnswer.query.filter_by(student_id=student.id).delete(synchronize_session=False)
                QuizReassessment.query.filter_by(student_id=student.id).delete(synchronize_session=False)
                ResourceAssignment.query.filter_by(student_id=student.id).delete(synchronize_session=False)
                
                # Flush to ensure dependent records are deleted
                db.session.flush()
                
                # Now delete study sessions
                StudySession.query.filter_by(student_id=student.id).delete(synchronize_session=False)
                
                # Finally delete the student record
                db.session.delete(student)

        # If deleting teacher, clean up teacher-related records
        elif user.role == 'teacher':
            # Delete teacher notifications
            TeacherNotification.query.filter_by(teacher_id=user.id).delete(synchronize_session=False)
            # Delete resource access records created by this teacher
            ResourceAccess.query.filter_by(created_by=user.id).delete(synchronize_session=False)
            # Delete quiz metadata created by this teacher
            QuizMetadata.query.filter_by(created_by=user.id).delete(synchronize_session=False)
            
            # Note: We don't delete resources/questions created by teacher as they might be in use
            # If you want to delete them too, you'd need to clean up all dependent records first

        # Delete the user account
        db.session.delete(user)
        db.session.commit()
        flash('User deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting user: {str(e)}', 'danger')

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/<int:id>/role', methods=['POST'])
@login_required
@admin_required
def admin_update_user_role(id):
    user = User.query.get_or_404(id)
    new_role = (request.form.get('role') or '').strip()

    if new_role not in ALLOWED_ROLES:
        flash('Invalid role.', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Prevent demoting the last admin
    if user.role == 'admin' and new_role != 'admin':
        admin_count = User.query.filter_by(role='admin').count()
        if admin_count <= 1:
            flash('Cannot change role of the last admin.', 'danger')
            return redirect(url_for('admin_dashboard'))

    # Prevent changing own role to avoid lockout surprises
    if user.id == current_user.id and new_role != 'admin':
        flash('You cannot change your own role to a non-admin while logged in.', 'warning')
        return redirect(url_for('admin_dashboard'))

    try:
        user.role = new_role
        db.session.commit()
        flash('User role updated.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to update role: {str(e)}', 'danger')
    return redirect(url_for('admin_dashboard'))

@app.route('/clear-session')
def clear_session():
    """Clear any existing session - useful for debugging"""
    logout_user()
    flash('Session cleared. You can now login again.', 'info')
    return redirect(url_for('login'))

# Admin utility: delete all accounts (dangerous)
@app.route('/admin/wipe-all', methods=['POST'])
@login_required
@admin_required
def admin_wipe_all():
    try:
        # Order matters due to FKs; delete dependent tables first
        StudentNotification.query.delete()
        TeacherNotification.query.delete()
        ResourceAssignment.query.delete()
        # Delete rows that reference study_session.id before deleting study_session
        ResourceEngagement.query.delete()
        StudentActivity.query.delete()
        StudentSuccessPrediction.query.delete()
        # Delete answers before questions (FK: student_answer.question_id -> question.id)
        StudentAnswer.query.delete()
        Question.query.delete()
        QuizReassessment.query.delete()
        QuizMetadata.query.delete()
        StudySession.query.delete()
        Resource.query.delete()
        StudentLearningProfile.query.delete()
        Student.query.delete()
        User.query.delete()
        db.session.commit()
        flash('All accounts and related data deleted.', 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Wipe failed: {str(e)}', 'danger')
    return redirect(url_for('admin_dashboard'))

@app.route('/teacher/dashboard')
@login_required
@teacher_required
def teacher_dashboard():
    # Get all grades this teacher teaches (from their own students)
    teacher_students = Student.query.filter_by(teacher_id=current_user.id).all()
    teacher_grades = list(set([s.grade for s in teacher_students]))
    # Get all students in these grades, regardless of who registered them
    students = Student.query.filter(Student.grade.in_(teacher_grades)).all()
    resources = Resource.query.filter_by(created_by=current_user.id).order_by(Resource.created_at.desc()).all()
    
    # Get performance statistics
    total_students = len(students)
    total_resources = len(resources)
    
    # Calculate average scores ONLY for quiz sessions
    student_ids = [student.id for student in students]
    avg_score = 0
    if student_ids:
        # Join StudySession -> Resource and restrict to resource_type == 'quiz'
        quiz_sessions_q = db.session.query(StudySession).join(Resource, Resource.id == StudySession.resource_id).filter(
            StudySession.student_id.in_(student_ids),
            StudySession.quiz_score.isnot(None),
            Resource.resource_type == 'quiz'
        )
        total_sessions = quiz_sessions_q.count()
        if total_sessions > 0:
            avg_score = db.session.query(db.func.avg(StudySession.quiz_score)).join(Resource, Resource.id == StudySession.resource_id).filter(
                StudySession.student_id.in_(student_ids),
                StudySession.quiz_score.isnot(None),
                Resource.resource_type == 'quiz'
            ).scalar()
    
    # Calculate average engagement time
    avg_engagement_time = 0
    if student_ids:
        avg_engagement = db.session.query(db.func.avg(ResourceEngagement.total_time_spent)).filter(ResourceEngagement.student_id.in_(student_ids)).scalar()
        if avg_engagement:
            avg_engagement_time = avg_engagement / 60  # Convert to minutes
    
    # Add sessions data to students for the template
    for student in students:
        student.sessions = StudySession.query.filter_by(student_id=student.id).order_by(StudySession.start_time.desc()).limit(5).all()
    
    return render_template('teacher_dashboard.html', 
                         students=students, 
                         resources=resources,
                         total_students=total_students,
                         total_resources=total_resources,
                         avg_score=avg_score,
                         avg_engagement_time=avg_engagement_time)

@app.route('/teacher/add_student', methods=['GET', 'POST'])
@login_required
@teacher_required
def add_student():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        student_id = request.form.get('student_id')
        grade = request.form.get('grade')
        username = request.form.get('username')
        password = request.form.get('password')

        if not all([name, email, student_id, grade, username, password]):
            flash('All fields are required.', 'danger')
            return redirect(url_for('add_student'))

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('add_student'))
        if email and db.session.query(User).filter(db.func.lower(User.email) == (email.lower())).first():
            flash('Email already in use.', 'danger')
            return redirect(url_for('add_student'))

        if Student.query.filter_by(student_id=student_id).first():
            flash('Student ID already exists.', 'danger')
            return redirect(url_for('add_student'))

        try:
            user = User(username=username, role='student', email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()

            student = Student(
                name=name,
                student_id=student_id,
                grade=grade,
                user_id=user.id,
                teacher_id=current_user.id
            )
            db.session.add(student)
            db.session.commit()

            flash('Student added successfully!', 'success')
            return redirect(url_for('teacher_dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding student: {str(e)}', 'danger')
            return redirect(url_for('add_student'))

    return render_template('add_student.html')

@app.route('/teacher/edit_student/<int:id>', methods=['GET', 'POST'])
@login_required
@teacher_required
def edit_student(id):
    student = Student.query.get_or_404(id)
    
    if request.method == 'POST':
        name = request.form.get('name')
        student_id = request.form.get('student_id')
        grade = request.form.get('grade')
        email = request.form.get('email')
        
        if not all([name, student_id, grade]):
            flash('All fields are required.', 'danger')
            return redirect(url_for('edit_student', id=id))
        
        # Check if student ID already exists for other students
        existing_student = Student.query.filter_by(student_id=student_id).first()
        if existing_student and existing_student.id != id:
            flash('Student ID already exists.', 'danger')
            return redirect(url_for('edit_student', id=id))
        
        try:
            student.name = name
            student.student_id = student_id
            student.grade = grade
            # Update linked user's email if available
            if email is not None and email.strip() and student.user_id:
                user = User.query.get(student.user_id)
                if user:
                    user.email = email.strip()
            
            db.session.commit()
            flash('Student updated successfully!', 'success')
            return redirect(url_for('teacher_dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating student: {str(e)}', 'danger')
            return redirect(url_for('edit_student', id=id))
    
    # Prefill email from linked user if present
    user_email = None
    if student.user_id:
        linked_user = User.query.get(student.user_id)
        if linked_user:
            user_email = linked_user.email
    return render_template('edit_student.html', student=student, user_email=user_email)

@app.route('/teacher/delete_student/<int:id>', methods=['POST'])
@login_required
@teacher_required
def delete_student(id):
    student = Student.query.get_or_404(id)
    try:
        # Eagerly delete dependent rows to satisfy FK constraints (SQLite lacks implicit cascades)
        sid = student.id

        # Notifications and profiles
        StudentNotification.query.filter_by(student_id=sid).delete(synchronize_session=False)
        TeacherNotification.query.filter_by(student_id=sid).delete(synchronize_session=False)
        StudentLearningProfile.query.filter_by(student_id=sid).delete(synchronize_session=False)
        StudentSuccessPrediction.query.filter_by(student_id=sid).delete(synchronize_session=False)

        # Sessions and session-linked rows
        _sess_rows = db.session.query(StudySession.id).filter_by(student_id=sid).all()
        session_ids = [row[0] for row in _sess_rows]
        if session_ids:
            StudentActivity.query.filter(StudentActivity.session_id.in_(session_ids)).delete(synchronize_session=False)
        # Also remove any activities not linked to a session but linked to the student
        StudentActivity.query.filter_by(student_id=sid).delete(synchronize_session=False)

        # Other direct dependencies
        ResourceEngagement.query.filter_by(student_id=sid).delete(synchronize_session=False)
        StudentAnswer.query.filter_by(student_id=sid).delete(synchronize_session=False)
        QuizReassessment.query.filter_by(student_id=sid).delete(synchronize_session=False)
        ResourceAssignment.query.filter_by(student_id=sid).delete(synchronize_session=False)

        # Finally delete sessions for this student
        StudySession.query.filter_by(student_id=sid).delete(synchronize_session=False)

        # Delete associated user account last
        if student.user_id:
            user = User.query.get(student.user_id)
            if user:
                db.session.delete(user)

        # Ensure dependent deletions are flushed before deleting the student
        db.session.flush()

        # Delete the student record
        db.session.delete(student)
        db.session.commit()
        flash('Student deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        app.logger.exception('Error deleting student')
        flash(f'Error deleting student: {str(e)}', 'danger')
    return redirect(url_for('teacher_dashboard'))

@app.route('/teacher/resources', methods=['GET', 'POST'])
@login_required
@teacher_required
def teacher_resources():
    if request.method == 'POST':
        # Verify CSRF token
        if not request.form.get('csrf_token') or request.form.get('csrf_token') != csrf._get_csrf_token():
            abort(400, 'Invalid CSRF token')
            
        # Ensure teacher has an email set for notifications
        if not (getattr(current_user, 'email', None)):
            flash('Please set your account email before uploading resources (needed for notifications).', 'danger')
            return redirect(url_for('teacher_resources'))
        title = request.form.get('title')
        description = request.form.get('description')
        resource_type = request.form.get('resource_type')
        grade = request.form.get('grade')
        access_time_limit_str = request.form.get('access_time_limit', '0')
        access_time_limit = int(access_time_limit_str) if access_time_limit_str.strip() else 0
        file_path = None
        url = None
        content = None
        extraction_failed = False
        
        if not all([title, resource_type, grade]):
            flash('Required fields are missing!', 'danger')
            return redirect(url_for('teacher_resources'))
        
        try:
            if resource_type in ['note', 'video']:
                if 'file' not in request.files:
                    flash('No file uploaded!', 'danger')
                    return redirect(url_for('teacher_resources'))
                file = request.files['file']
                if file.filename == '':
                    flash('No file selected!', 'danger')
                    return redirect(url_for('teacher_resources'))
                upload_dir = os.path.join(app.root_path, 'static', 'uploads')
                os.makedirs(upload_dir, exist_ok=True)
                filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
                
                # Handle DOC/DOCX conversion to PDF for notes
                original_path = os.path.join(upload_dir, filename)
                file.save(original_path)
                
                if resource_type == 'note' and (file.filename.lower().endswith('.doc') or file.filename.lower().endswith('.docx')):
                    # For DOC/DOCX files, we'll serve them as HTML content for inline viewing
                    # This ensures tracking works without external conversion dependencies
                    try:
                        # Extract text content from DOC/DOCX
                        content = extract_text_from_docx(original_path)
                        
                        # Create an HTML version for inline viewing
                        html_filename = filename.rsplit('.', 1)[0] + '.html'
                        html_path = os.path.join(upload_dir, html_filename)
                        
                        # Generate HTML content with proper formatting
                        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }}
        h1 {{ color: #333; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .content {{ max-width: 800px; margin: 0 auto; }}
        p {{ margin-bottom: 15px; }}
    </style>
</head>
<body>
    <div class="content">
        <h1>{title}</h1>
        <p><strong>Description:</strong> {description}</p>
        <hr>
        <div class="document-content">
                            {content.replace(chr(10), '<br>').replace(chr(13), '')}
        </div>
    </div>
</body>
</html>"""
                        
                        # Save HTML file
                        with open(html_path, 'w', encoding='utf-8') as f:
                            f.write(html_content)
                        
                        # Remove original DOC/DOCX file
                        os.remove(original_path)
                        
                        # Update filename and path to point to HTML
                        filename = html_filename
                        file_path = f"uploads/{filename}"
                        
                        # Store the extracted text content for search
                        content = f"Resource Title: {title}\nDescription: {description}\nContent: {content}"
                        
                    except Exception as e:
                        flash(f'Error processing document: {str(e)}. File will be served as-is.', 'warning')
                        # Keep original file on error and continue without generating questions
                        file_path = f"uploads/{filename}"
                        content = f"Resource Title: {title}\nDescription: {description}"
                        extraction_failed = True
                else:
                    # Normalize to forward slashes for URLs
                    file_path = f"uploads/{filename}"
                    
                    if resource_type == 'note' and file.filename.lower().endswith('.pdf'):
                        try:
                            content = extract_text_from_pdf(original_path)
                        except Exception as e:
                            flash(f'Error processing PDF: {str(e)}. File will be served as-is.', 'warning')
                            content = f"Resource Title: {title}\nDescription: {description}"
                            extraction_failed = True
                    else:
                        content = f"Resource Title: {title}\nDescription: {description}"
            elif resource_type == 'link':
                url = request.form.get('url')
                if not url:
                    flash('URL is required for links!', 'danger')
                    return redirect(url_for('teacher_resources'))
                content = f"Resource Title: {title}\nDescription: {description}\nURL: {url}"
            # Create resource and commit
            resource = Resource(
                title=title,
                description=description,
                resource_type=resource_type,
                file_path=file_path,
                url=url,
                created_by=current_user.id,
                grade=grade,
                access_time_limit=access_time_limit
            )
            db.session.add(resource)
            db.session.flush()  # Get resource.id
            db.session.commit()

            try:
                trigger_resource_notification_async(resource.id)
            except Exception:
                pass

            # Redirect teacher to manual quiz creation attached to this resource
            flash('Resource added successfully. Now create a quiz for it.', 'success')
            return redirect(url_for('create_quiz', resource_id=resource.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding resource: {str(e)}', 'danger')
            return redirect(url_for('teacher_resources'))
    resources = Resource.query.filter_by(created_by=current_user.id, is_deleted=False).order_by(Resource.created_at.desc()).all()
    quiz_counts = {}
    access_keys = {}
    for resource in resources:
        quiz_counts[resource.id] = db.session.query(Question).filter_by(resource_id=resource.id).count()
        access_keys[resource.id] = db.session.query(ResourceAssignment).filter_by(resource_id=resource.id).count()
    return render_template('teacher_resources.html', resources=resources, quiz_counts=quiz_counts, access_keys=access_keys)

@app.route('/teacher/grant_reassessment/<int:student_id>/<int:resource_id>', methods=['POST', 'GET'])
@login_required
def grant_reassessment(student_id, resource_id):
    if current_user.role != 'teacher':
        abort(403)
    
    student = Student.query.get_or_404(student_id)
    resource = Resource.query.get_or_404(resource_id)
    
    # For GET requests, show confirmation page
    if request.method == 'GET':
        return render_template('grant_reassessment_confirm.html', 
                             student=student, 
                             resource=resource,
                             student_id=student_id,
                             resource_id=resource_id)
    
    # Check if student has completed this quiz
    completed_session = StudySession.query.filter_by(
        student_id=student_id,
        resource_id=resource_id,
        completed=True
    ).first()
    
    if not completed_session:
        flash('Student has not completed this quiz yet.', 'error')
        return redirect(request.referrer or url_for('teacher_dashboard'))
    
    # Check if reassessment already exists
    existing_reassessment = QuizReassessment.query.filter_by(
        student_id=student_id,
        resource_id=resource_id,
        is_used=False
    ).first()
    
    if existing_reassessment:
        flash('Reassessment permission already granted for this student.', 'warning')
        return redirect(request.referrer or url_for('teacher_dashboard'))
    
    # Create new reassessment permission
    reason = request.form.get('reason', 'Teacher granted reassessment')
    reassessment = QuizReassessment(
        student_id=student_id,
        resource_id=resource_id,
        granted_by=current_user.id,
        reason=reason
    )
    
    db.session.add(reassessment)
    db.session.commit()
    
    flash(f'Reassessment permission granted to {student.name} for {resource.title}.', 'success')
    return redirect(request.referrer or url_for('teacher_dashboard'))

@app.route('/teacher/test_route')
@login_required
def test_route():
    return "Test route working!"

@app.route('/teacher/review_questions', methods=['GET'])
@login_required
@teacher_required
def review_questions():
    resource = session.get('pending_resource')
    questions = session.get('pending_questions')
    if not resource or questions is None:
        flash('No pending questions to review.', 'warning')
        return redirect(url_for('teacher_resources'))
    return render_template('review_questions.html', resource_title=resource['title'], resource_id=resource['id'], questions=questions)

@app.route('/teacher/approve_questions', methods=['POST'])
@login_required
@teacher_required
def approve_questions():
    abort(404)

@app.route('/teacher/regenerate_questions', methods=['POST'])
@login_required
@teacher_required
def regenerate_questions():
    abort(404)

@app.route('/teacher/student_progress')
@login_required
@teacher_required
def teacher_student_progress():
    students = Student.query.filter_by(teacher_id=current_user.id).all()
    progress_data = []
    
    for student in students:
        # Get all study sessions for this student
        sessions = StudySession.query.filter_by(student_id=student.id).all()
        student_data = {
            'id': student.id,
            'name': student.name,
            'student_id': student.student_id,
            'grade': student.grade,
            'sessions': []
        }
        
        for session in sessions:
            resource = Resource.query.get(session.resource_id)
            if resource:  # Only add session if resource exists
                # For quiz resources, use session data
                if resource.resource_type == 'quiz':
                    # Generate teacher-specific recommendation for quiz results
                    if session.quiz_score is not None:
                        if session.quiz_score >= 80:
                            teacher_rec = f"Excellent work by {student.name}! Consider providing advanced materials or leadership opportunities."
                        elif session.quiz_score >= 60:
                            teacher_rec = f"Good progress by {student.name}. Provide positive reinforcement and consider additional practice materials."
                        else:
                            teacher_rec = f"Provide {student.name} with additional support. Consider one-on-one guidance, remedial materials, or reassessment opportunities."
                    else:
                        teacher_rec = f"No quiz score available for {student.name}. Check if they completed the quiz."
                    
                    session_data = {
                        'resource_title': resource.title,
                        'resource_id': resource.id,
                        'resource_type': resource.resource_type,
                        'duration': session.duration,
                        'quiz_score': session.quiz_score if session.quiz_score is not None else None,
                        'completion_percentage': session.quiz_score if session.quiz_score is not None else 0,
                        'completed': session.completed,
                        'ai_recommendation': teacher_rec,
                        'date': session.start_time.strftime('%B %d, %Y at %I:%M %p')
                    }
                else:
                    # For non-quiz resources (notes, videos, links), calculate from engagement data
                    engagement = ResourceEngagement.query.filter_by(
                        student_id=student.id,
                        resource_id=resource.id,
                        session_id=session.id
                    ).first()
                    
                    if engagement:
                        # Calculate duration from total_time_spent
                        duration_seconds = engagement.total_time_spent or 0
                        duration_str = f"{duration_seconds // 3600}h {(duration_seconds % 3600) // 60}m {duration_seconds % 60}s" if duration_seconds > 0 else "Not recorded"
                        
                        # Determine completion based on engagement metrics
                        is_completed = False
                        completion_percentage = 0
                        
                        if resource.resource_type == 'video':
                            # Video is completed if watched for at least 80% of duration or 5 minutes
                            is_completed = duration_seconds >= 300 or (engagement.scroll_depth or 0) >= 80
                            completion_percentage = min(100, (duration_seconds / 300) * 100) if duration_seconds > 0 else 0
                        elif resource.resource_type == 'note':
                            # Note is completed if read for at least 2 minutes or scrolled through 70%
                            is_completed = duration_seconds >= 120 or (engagement.scroll_depth or 0) >= 70
                            completion_percentage = min(100, (duration_seconds / 120) * 100) if duration_seconds > 0 else 0
                        elif resource.resource_type == 'link':
                            # Link is completed if clicked and spent at least 30 seconds
                            is_completed = duration_seconds >= 30 or (engagement.clicks or 0) > 0
                            completion_percentage = min(100, (duration_seconds / 30) * 100) if duration_seconds > 0 else 0
                        
                        # Generate AI recommendation based on engagement data
                        ai_recommendation = generate_student_recommendation(engagement, resource.resource_type)
                        
                        session_data = {
                            'resource_title': resource.title,
                            'resource_id': resource.id,
                            'resource_type': resource.resource_type,
                            'duration': duration_str,
                            'quiz_score': None,  # No quiz score for non-quiz resources
                            'completed': is_completed,
                            'completion_percentage': completion_percentage,
                            'ai_recommendation': ai_recommendation,
                            'date': session.start_time.strftime('%B %d, %Y at %I:%M %p')
                        }
                    else:
                        # No engagement data found
                        session_data = {
                            'resource_title': resource.title,
                            'resource_id': resource.id,
                            'resource_type': resource.resource_type,
                            'duration': "Not recorded",
                            'quiz_score': None,
                            'completed': False,
                            'completion_percentage': 0,
                            'ai_recommendation': "No recommendation",
                            'date': session.start_time.strftime('%B %d, %Y at %I:%M %p')
                        }
                
                student_data['sessions'].append(session_data)
        
        progress_data.append(student_data)
    
    return render_template('student_progress.html', progress_data=progress_data)

@app.route('/student/dashboard')
@login_required
def student_dashboard():
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get all resources uploaded by the student's teacher for their grade
    # Include legacy "attached quiz" resources that were mistakenly marked as 'quiz' but have files/URLs
    teacher_resources = db.session.query(Resource).filter(
        Resource.created_by == student.teacher_id,
        Resource.grade == student.grade,
        Resource.is_deleted == False,
        (
            (Resource.resource_type != 'quiz') |
            ((Resource.file_path.isnot(None)) | (Resource.url.isnot(None)))
        )
    ).order_by(Resource.created_at.desc()).all()
    
    # Get existing assignments for this student to show access key status
    existing_assignments = db.session.query(ResourceAssignment).filter(
        ResourceAssignment.student_id == student.id
    ).all()
    
    # Create a mapping of resource_id to assignment for quick lookup
    assignment_map = {assignment.resource_id: assignment for assignment in existing_assignments}
    
    # Create assignments list with all teacher resources, showing assignment status
    assignments = []
    for resource in teacher_resources:
        assignment = assignment_map.get(resource.id)
        if assignment:
            # Student already has an assignment
            assignments.append((resource, assignment))
        else:
            # Create a virtual assignment object for display purposes
            virtual_assignment = type('VirtualAssignment', (), {
                'access_key': None,
                'max_students': 1,
                'assigned_by': student.teacher_id,
                'assigned_at': resource.created_at
            })()
            assignments.append((resource, virtual_assignment))
    
    # Get all potential quizzes (standalone quiz resources OR resources with attached questions)
    available_quizzes_raw = db.session.query(Resource, QuizMetadata, db.func.count(Question.id).label('question_count')).outerjoin(
        QuizMetadata, Resource.id == QuizMetadata.resource_id
    ).outerjoin(
        Question, Resource.id == Question.resource_id
    ).filter(
        Resource.created_by == student.teacher_id,
        Resource.grade == student.grade,
        Resource.is_deleted == False
    ).group_by(Resource.id).order_by(Resource.created_at.desc()).all()
    
    # Filter out any None values and ensure we have valid quiz data
    available_quizzes = []
    for quiz_data in available_quizzes_raw:
        resource, metadata, question_count = quiz_data
        if resource is not None and (resource.resource_type == 'quiz' or (question_count or 0) > 0):  # Ensure resource is quiz or has questions
            # Check if student has completed this quiz
            completed_session = StudySession.query.filter_by(
                student_id=student.id,
                resource_id=resource.id,
                completed=True
            ).first()
            
            # Check if teacher has allowed reassessment
            reassessment = QuizReassessment.query.filter_by(
                student_id=student.id,
                resource_id=resource.id,
                is_used=False
            ).first()
            
            available_quizzes.append({
                'resource': resource,
                'metadata': metadata,
                'question_count': question_count or 0,
                'completed': bool(completed_session),
                'has_reassessment': bool(reassessment),
                'completed_session': completed_session
            })
    
    # Get recent quiz sessions with results and AI recommendations
    recent_sessions = []
    sessions = StudySession.query.filter_by(
        student_id=student.id,
        completed=True
    ).order_by(StudySession.end_time.desc()).limit(5).all()
    
    for session in sessions:
        resource = Resource.query.get(session.resource_id)
        if resource and session.quiz_score is not None:
            # Check if marks are published for this quiz
            metadata = QuizMetadata.query.filter_by(resource_id=resource.id).first()
            marks_published = metadata and metadata.marks_published if metadata else False
            
            # Get detailed marks if published
            detailed_marks = None
            if marks_published:
                questions = Question.query.filter_by(resource_id=resource.id).all()
                student_answers = {}
                for question in questions:
                    answer = StudentAnswer.query.filter_by(
                        student_id=student.id,
                        question_id=question.id
                    ).first()
                    if answer:
                        student_answers[question.id] = answer
                
                # Calculate detailed marks
                total_marks = sum(q.marks for q in questions)
                earned_marks = 0
                for question in questions:
                    if question.id in student_answers:
                        answer = student_answers[question.id]
                        if question.question_type == 'mcq':
                            if answer.is_correct:
                                earned_marks += question.marks
                        else:
                            if answer.marks_awarded is not None:
                                earned_marks += answer.marks_awarded
                
                detailed_marks = {
                    'total_marks': total_marks,
                    'earned_marks': earned_marks,
                    'percentage': (earned_marks / total_marks * 100) if total_marks > 0 else 0
                }
            
            # Determine if the resource for this session is a quiz
            try:
                resource = Resource.query.get(session.resource_id)
                is_quiz = bool(resource and resource.resource_type == 'quiz')
            except Exception:
                resource = None
                is_quiz = False

            session_data = {
                'resource_title': resource.title,
                'quiz_score': session.quiz_score,
                'ai_recommendation': session.ai_recommendation,
                'date': session.end_time.strftime('%B %d, %Y at %I:%M %p') if session.end_time else 'Unknown',
                'session_id': session.id,
                'marks_published': marks_published,
                'detailed_marks': detailed_marks,
                'resource_id': resource.id if resource else None,
                'is_quiz': is_quiz
            }
            recent_sessions.append(session_data)
    
    # Calculate quiz statistics
    quiz_stats = None
    completed_sessions = StudySession.query.filter_by(
        student_id=student.id,
        completed=True
    ).filter(StudySession.quiz_score.isnot(None)).all()
    
    if completed_sessions:
        scores = [session.quiz_score for session in completed_sessions if session.quiz_score is not None]
        total_quizzes = len(available_quizzes)
        completed_quizzes = len(completed_sessions)
        average_score = sum(scores) / len(scores) if scores else 0
        
        quiz_stats = type('QuizStats', (), {
            'average_score': average_score,
            'completed_quizzes': completed_quizzes,
            'total_quizzes': total_quizzes
        })()
    
    return render_template('student_dashboard.html', 
                         student=student, 
                         assignments=assignments, 
                         available_quizzes=available_quizzes,
                         recent_sessions=recent_sessions,
                         quiz_stats=quiz_stats)

@app.route('/student/resources')
@login_required
def student_resources():
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    # All resources from this student's teacher for their grade
    # Include legacy "attached quiz" resources with files/URLs even if type was set to 'quiz'
    teacher_resources = db.session.query(Resource).filter(
        Resource.created_by == student.teacher_id,
        Resource.grade == student.grade,
        Resource.is_deleted == False,
        (
            (Resource.resource_type != 'quiz') |
            ((Resource.file_path.isnot(None)) | (Resource.url.isnot(None)))
        )
    ).order_by(Resource.created_at.desc()).all()
    
    # Existing assignments to indicate access method
    existing_assignments = db.session.query(ResourceAssignment).filter(
        ResourceAssignment.student_id == student.id
    ).all()
    assignment_map = {assignment.resource_id: assignment for assignment in existing_assignments}
    
    assignments = []
    for resource in teacher_resources:
        assignment = assignment_map.get(resource.id)
        if assignment:
            assignments.append((resource, assignment))
        else:
            virtual_assignment = type('VirtualAssignment', (), {
                'access_key': None,
                'max_students': 1,
                'assigned_by': student.teacher_id,
                'assigned_at': resource.created_at
            })()
            assignments.append((resource, virtual_assignment))
    
    return render_template('student_resources.html', student=student, assignments=assignments)

@app.route('/test/notifications-simple')
@login_required
def test_notifications_simple():
    if current_user.role != 'student':
        return "Not a student", 403
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return "Student not found", 404
    
    # Get unread notifications
    notifications = db.session.query(StudentNotification).filter_by(student_id=student.id, is_read=False).all()
    
    # Create a test notification if none exist
    if len(notifications) == 0:
        test_notification = StudentNotification(
            student_id=student.id,
            title="Test Notification",
            message="This is a test notification created at " + str(datetime.now()),
            is_read=False
        )
        db.session.add(test_notification)
        db.session.commit()
        notifications = [test_notification]
    
    # Return simple HTML
    html = f"<h1>Notifications for Student {student.id}</h1>"
    html += f"<p>Found {len(notifications)} notifications:</p>"
    html += "<ul>"
    for n in notifications:
        html += f"<li><strong>{n.title}</strong>: {n.message} (ID: {n.id}, Read: {n.is_read})</li>"
    html += "</ul>"
    html += f'<p><a href="{url_for("student_notifications")}">Go to notification page</a></p>'
    
    return html

@app.route('/student/notifications-simple')
@login_required
def student_notifications_simple():
    if current_user.role != 'student':
        abort(403)
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get only unread notifications for the student, ordered by newest first
    notifications = db.session.query(StudentNotification).filter_by(student_id=student.id, is_read=False).order_by(StudentNotification.created_at.desc()).all()
    
    # If no notifications, create a test one for debugging
    if len(notifications) == 0:
        test_notification = StudentNotification(
            student_id=student.id,
            title="Test Notification",
            message="This is a test notification created automatically for debugging.",
            is_read=False
        )
        db.session.add(test_notification)
        db.session.commit()
        notifications = [test_notification]
    
    return render_template('student_notifications_simple.html', student=student, notifications=notifications)

@app.route('/test/notifications')
@login_required
def test_notifications():
    if current_user.role != 'student':
        return "Not a student", 403
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return "Student not found", 404
    
    # Get unread notifications
    notifications = db.session.query(StudentNotification).filter_by(student_id=student.id, is_read=False).all()
    
    result = f"Student {student.id} has {len(notifications)} unread notifications:\n"
    for n in notifications:
        result += f"- ID: {n.id}, Title: {n.title}, Read: {n.is_read}\n"
    
    return result

@app.route('/test/create_notification')
@login_required
def create_test_notification():
    if current_user.role != 'student':
        return "Not a student", 403
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return "Student not found", 404
    
    # Create a test notification
    test_notification = StudentNotification(
        student_id=student.id,
        title="Test Notification",
        message="This is a test notification created at " + str(datetime.now()),
        is_read=False
    )
    db.session.add(test_notification)
    db.session.commit()
    
    return f"Created test notification with ID: {test_notification.id}"

@app.route('/student/notifications')
@login_required
def student_notifications():
    if current_user.role != 'student':
        abort(403)
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get only unread notifications for the student, ordered by newest first
    notifications = db.session.query(StudentNotification).filter_by(student_id=student.id, is_read=False).order_by(StudentNotification.created_at.desc()).all()
    
    # Debug: print notification count and details
    print(f"Student {student.id} has {len(notifications)} unread notifications")
    for n in notifications:
        print(f"  - Notification {n.id}: {n.title}")
    
    # If no notifications, create a test one for debugging
    if len(notifications) == 0:
        print("No notifications found, creating a test notification...")
        test_notification = StudentNotification(
            student_id=student.id,
            title="Test Notification",
            message="This is a test notification created automatically for debugging.",
            is_read=False
        )
        db.session.add(test_notification)
        db.session.commit()
        notifications = [test_notification]
        print(f"Created test notification with ID: {test_notification.id}")
    
    return render_template('student_notifications.html', student=student, notifications=notifications)

@app.route('/student/notifications/<int:notification_id>/read', methods=['POST'])
@login_required
def mark_student_notification_read(notification_id):
    if current_user.role != 'student':
        abort(403)
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    notification = db.session.query(StudentNotification).filter_by(id=notification_id, student_id=student.id).first_or_404()
    try:
        # Delete the notification instead of marking as read
        db.session.delete(notification)
        db.session.commit()
    except Exception:
        db.session.rollback()
    return redirect(url_for('student_notifications'))

# Unified Notifications API for both students and teachers
@app.route('/api/notifications')
@login_required
def api_notifications():
    try:
        notifications_payload = []
        unread_count = 0
        role = getattr(current_user, 'role', None)

        if role == 'student':
            student = Student.query.filter_by(user_id=current_user.id).first()
            if not student:
                return jsonify({"success": False, "error": "Student not found"}), 404
            notifications = db.session.query(StudentNotification).filter_by(student_id=student.id, is_read=False).order_by(StudentNotification.created_at.desc()).limit(50).all()
            unread_count = len(notifications)
            for n in notifications:
                notifications_payload.append({
                    'id': n.id,
                    'title': n.title or 'Notification',
                    'message': n.message or '',
                    'created_at': n.created_at.strftime('%B %d, %Y %I:%M %p') if n.created_at else '',
                    'resource_id': n.resource_id,
                    'resource_url': url_for('view_resource', resource_id=n.resource_id) if n.resource_id else None,
                    'is_read': n.is_read
                })
        elif role == 'teacher':
            notifications = db.session.query(TeacherNotification).filter_by(teacher_id=current_user.id, is_read=False).order_by(TeacherNotification.created_at.desc()).limit(50).all()
            unread_count = len(notifications)
            for n in notifications:
                # For teachers, provide appropriate URLs based on resource type
                resource_url = None
                if n.resource_id:
                    resource = Resource.query.get(n.resource_id)
                    if resource:
                        if resource.resource_type == 'quiz':
                            resource_url = url_for('quiz_results', quiz_id=n.resource_id)
                        else:
                            resource_url = url_for('teacher_resources')
                
                notifications_payload.append({
                    'id': n.id,
                    'title': n.title or 'Notification',
                    'message': n.message or '',
                    'created_at': n.created_at.strftime('%B %d, %Y %I:%M %p') if n.created_at else '',
                    'resource_id': n.resource_id,
                    'resource_url': resource_url,
                    'is_read': n.is_read
                })
        else:
            # Admin or unknown role: no notifications for now
            notifications_payload = []
            unread_count = 0

        return jsonify({
            'success': True,
            'role': role,
            'unread_count': unread_count,
            'notifications': notifications_payload
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
@login_required
def api_notifications_mark_read(notification_id):
    role = getattr(current_user, 'role', None)
    try:
        if role == 'student':
            student = Student.query.filter_by(user_id=current_user.id).first()
            if not student:
                return jsonify({"success": False, "error": "Student not found"}), 404
            notification = db.session.query(StudentNotification).filter_by(id=notification_id, student_id=student.id).first_or_404()
        elif role == 'teacher':
            notification = db.session.query(TeacherNotification).filter_by(id=notification_id, teacher_id=current_user.id).first_or_404()
        else:
            return jsonify({"success": False, "error": "Unsupported role"}), 400

        notification.is_read = True
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

# Test route to create notifications
@app.route('/test/create_notification')
@login_required
def test_create_notification():
    try:
        role = getattr(current_user, 'role', None)
        if role == 'student':
            student = Student.query.filter_by(user_id=current_user.id).first()
            if not student:
                return "Student not found", 404
            
            # Create a test notification
            notification = StudentNotification(
                student_id=student.id,
                title="Test Notification",
                message="This is a test notification to verify the notification system is working properly.",
                is_read=False
            )
            db.session.add(notification)
            db.session.commit()
            return f"Created test notification for student {student.name} (ID: {notification.id})"
            
        elif role == 'teacher':
            # Create a test notification for teacher
            notification = TeacherNotification(
                teacher_id=current_user.id,
                title="Test Teacher Notification",
                message="This is a test notification for teachers to verify the notification system is working properly.",
                is_read=False
            )
            db.session.add(notification)
            db.session.commit()
            return f"Created test notification for teacher {current_user.username} (ID: {notification.id})"
        else:
            return "Unsupported role", 400
    except Exception as e:
        db.session.rollback()
        return f"Error creating notification: {str(e)}", 500
@app.route('/student/my_progress')
@login_required
@student_required
def student_my_progress():
    """Student view of their own progress and recommendations"""
    # Get the Student object linked to the current user
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get all study sessions for this student
    sessions = StudySession.query.filter_by(student_id=student.id).order_by(StudySession.start_time.desc()).all()
    
    progress_data = []
    total_sessions = 0
    total_score = 0
    total_completion = 0
    
    for session in sessions:
        resource = Resource.query.get(session.resource_id)
        if not resource:
            continue
            
        total_sessions += 1
        
        # For quiz resources, use session data
        if resource.resource_type == 'quiz':
            session_data = {
                'resource_title': resource.title,
                'resource_id': resource.id,
                'resource_type': resource.resource_type,
                'duration': session.duration,
                'quiz_score': session.quiz_score if session.quiz_score is not None else None,
                'completion_percentage': session.quiz_score if session.quiz_score is not None else 0,
                'completed': session.completed,
                'ai_recommendation': session.ai_recommendation,
                'date': session.start_time.strftime('%B %d, %Y at %I:%M %p')
            }
            if session.quiz_score is not None:
                total_score += session.quiz_score
        else:
            # For non-quiz resources (notes, videos, links), calculate from engagement data
            engagement = ResourceEngagement.query.filter_by(
                student_id=student.id,
                resource_id=resource.id,
                session_id=session.id
            ).first()
            
            if engagement:
                # Calculate duration from tracking data
                duration_seconds = engagement.total_time_spent or 0
                hours = duration_seconds // 3600
                minutes = (duration_seconds % 3600) // 60
                seconds = duration_seconds % 60
                
                if hours > 0:
                    duration_str = f"{hours}h {minutes}m {seconds}s"
                elif minutes > 0:
                    duration_str = f"{minutes}m {seconds}s"
                else:
                    duration_str = f"{seconds}s"
                
                # Determine completion status based on resource type and engagement
                is_completed = False
                completion_percentage = 0
                
                if resource.resource_type == 'video':
                    # Video completed if watched for at least 5 minutes or 80% scroll depth
                    is_completed = (duration_seconds >= 300) or (engagement.scroll_depth or 0) >= 80
                    completion_percentage = min(100, (duration_seconds / 300) * 100) if duration_seconds < 300 else 100
                elif resource.resource_type == 'note':
                    # Note completed if read for at least 2 minutes or 70% scroll depth
                    is_completed = (duration_seconds >= 120) or (engagement.scroll_depth or 0) >= 70
                    completion_percentage = min(100, (engagement.scroll_depth or 0))
                elif resource.resource_type == 'link':
                    # Link completed if spent at least 1 minute and clicked
                    is_completed = (duration_seconds >= 60) and (engagement.clicks or 0) > 0
                    completion_percentage = min(100, (duration_seconds / 60) * 100) if duration_seconds < 60 else 100
                
                # Generate AI recommendation based on engagement data
                ai_recommendation = generate_student_recommendation(engagement, resource.resource_type)
                
                session_data = {
                    'resource_title': resource.title,
                    'resource_id': resource.id,
                    'resource_type': resource.resource_type,
                    'duration': duration_str,
                    'quiz_score': None,
                    'completion_percentage': round(completion_percentage, 1),
                    'completed': is_completed,
                    'ai_recommendation': ai_recommendation,
                    'date': session.start_time.strftime('%B %d, %Y at %I:%M %p')
                }
                total_completion += completion_percentage
            else:
                # No engagement data available
                session_data = {
                    'resource_title': resource.title,
                    'resource_id': resource.id,
                    'resource_type': resource.resource_type,
                    'duration': '0s',
                    'quiz_score': None,
                    'completion_percentage': 0,
                    'completed': False,
                    'ai_recommendation': 'No activity data available',
                    'date': session.start_time.strftime('%B %d, %Y at %I:%M %p')
                }
        
        progress_data.append(session_data)
    
    # Calculate summary statistics
    if total_sessions > 0:
        avg_score = (total_score + total_completion) / total_sessions
        completed_count = sum(1 for data in progress_data if data['completed'])
        completion_rate = (completed_count / total_sessions) * 100
    else:
        avg_score = 0
        completion_rate = 0
    
    summary_stats = {
        'total_sessions': total_sessions,
        'avg_score': round(avg_score, 1),
        'completion_rate': round(completion_rate, 1)
    }
    
    return render_template('student_my_progress.html', progress_data=progress_data, summary_stats=summary_stats, student=student)

@app.route('/student/ml_insights')
@login_required
def student_ml_insights():
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get all completed sessions for this student
    completed_sessions = StudySession.query.filter_by(
        student_id=student.id,
        completed=True
    ).filter(StudySession.quiz_score.isnot(None)).order_by(StudySession.end_time.desc()).all()
    
    # Calculate ML insights
    ml_insights = []
    performance_trend = []
    
    for session in completed_sessions:
        resource = Resource.query.get(session.resource_id)
        if resource:
            # Get enhanced AI recommendation for this session
            try:
                ai_recommendation = generate_ai_recommendation(session)
                
                # Parse AI recommendation to extract action and strategy
                if ai_recommendation:
                    # Determine action based on score
                    if session.quiz_score and session.quiz_score >= 80:
                        action = 'advance'
                        action_display = 'Ready to Advance'
                    elif session.quiz_score and session.quiz_score >= 60:
                        action = 'practice_related'
                        action_display = 'Continue Practice'
                    else:
                        action = 'review_prerequisites'
                        action_display = 'Review Fundamentals'
                    
                    # Use AI recommendation as strategy
                    strategy = ai_recommendation
                    
                    # Calculate confidence based on score consistency
                    if session.quiz_score and (session.quiz_score >= 90 or session.quiz_score <= 10):
                        confidence = 'High'
                    elif session.quiz_score and (session.quiz_score >= 70 or session.quiz_score <= 30):
                        confidence = 'Medium'
                    else:
                        confidence = 'Low'
                    
                    # Calculate success probability based on score
                    success_prob = min(max((session.quiz_score or 0) / 100.0, 0.0), 1.0)
                    
                else:
                    # Fallback to ML recommendation if AI fails
                    summary = {
                        'duration': session.duration or 0,
                        'quiz_score': session.quiz_score or 0.0,
                        'completed': bool(session.completed),
                    }
                    rec = ml_recommend(summary)
                    action = rec.get('recommended_action', 'unknown')
                    action_display = action.replace('_', ' ').title()
                    strategy = rec.get('strategy', 'No specific strategy available')
                    confidence = rec.get('confidence_level', 'Low')
                    success_prob = rec.get('success_probability', 0)
                    
            except Exception as e:
                print(f"Error generating AI recommendation for session {session.id}: {str(e)}")
                # Fallback to ML recommendation
                summary = {
                    'duration': session.duration or 0,
                    'quiz_score': session.quiz_score or 0.0,
                    'completed': bool(session.completed),
                }
                rec = ml_recommend(summary)
                action = rec.get('recommended_action', 'unknown')
                action_display = action.replace('_', ' ').title()
                strategy = rec.get('strategy', 'No specific strategy available')
                confidence = rec.get('confidence_level', 'Low')
                success_prob = rec.get('success_probability', 0)
            
            ml_insights.append({
                'resource_title': resource.title,
                'quiz_score': session.quiz_score,
                'success_probability': success_prob,
                'recommended_action': action,
                'action_display': action_display,
                'strategy': strategy,
                'confidence_level': confidence,
                'date': session.end_time.strftime('%B %d, %Y') if session.end_time else 'Unknown',
                'duration_minutes': (session.duration or 0) // 60
            })
        
        # Add to performance trend
        performance_trend.append({
            'date': session.end_time.strftime('%Y-%m-%d') if session.end_time else 'Unknown',
            'score': session.quiz_score,
            'probability': success_prob * 100 if success_prob else 0
        })
    
    # Calculate overall statistics
    if completed_sessions:
        scores = [s.quiz_score for s in completed_sessions if s.quiz_score is not None]
        avg_score = sum(scores) / len(scores) if scores else 0
        
        probabilities = [insight['success_probability'] for insight in ml_insights if insight['success_probability']]
        avg_probability = (sum(probabilities) / len(probabilities)) * 100 if probabilities else 0
        
        # Determine overall recommendation
        if avg_probability >= 80:
            overall_status = 'excellent'
            overall_message = 'You\'re performing excellently! Ready for more challenging material.'
        elif avg_probability >= 60:
            overall_status = 'good'
            overall_message = 'Good progress! Continue practicing to strengthen your understanding.'
        elif avg_probability >= 40:
            overall_status = 'needs_improvement'
            overall_message = 'Keep working! Focus on reviewing concepts and ask for help when needed.'
        else:
            overall_status = 'struggling'
            overall_message = 'Consider reviewing prerequisite materials and seeking additional support.'
    else:
        avg_score = 0
        avg_probability = 0
        overall_status = 'no_data'
        overall_message = 'Complete some quizzes to see your ML-powered insights!'
    
    # Get ML model info
    from ml_service import get_model_info
    model_info = get_model_info()
    
    return render_template('student_ml_insights.html',
                         student=student,
                         ml_insights=ml_insights[:10],  # Show last 10
                         performance_trend=performance_trend[-10:],  # Last 10 for chart
                         avg_score=avg_score,
                         avg_probability=avg_probability,
                         overall_status=overall_status,
                         overall_message=overall_message,
                         total_sessions=len(completed_sessions),
                         model_info=model_info)

@app.route('/student/quiz_review/<int:session_id>')
@login_required
def quiz_review(session_id):
    try:
        if current_user.role != 'student':
            abort(403)
        
        student = Student.query.filter_by(user_id=current_user.id).first()
        if not student:
            abort(404)
        
        # Get the study session
        session = StudySession.query.filter_by(
            id=session_id,
            student_id=student.id,
            completed=True
        ).first()
        
        if not session:
            abort(404)
        
        resource = Resource.query.get(session.resource_id)
        if not resource or resource.resource_type != 'quiz':
            abort(404)
        
        # Block review until marks are published
        metadata = QuizMetadata.query.filter_by(resource_id=resource.id).first()
        if not (metadata and metadata.marks_published):
            flash("Review will be available after your teacher publishes marks.", 'info')
            return redirect(url_for('student_quiz', resource_id=resource.id))

        # Get all questions for this quiz
        questions = Question.query.filter_by(resource_id=resource.id).order_by(Question.id).all()
        
        # Get student's answers for this session
        # Since StudentAnswer doesn't have session_id, we need to get answers by student and questions from this resource
        question_ids = [q.id for q in questions]
        answers = StudentAnswer.query.filter(
            StudentAnswer.student_id == student.id,
            StudentAnswer.question_id.in_(question_ids)
        ).all()
        
        # Create a mapping of question_id to answer
        answer_map = {answer.question_id: answer for answer in answers}
        
        # Combine questions with their answers
        question_answers = []
        correct_count = 0
        
        for question in questions:
            answer = answer_map.get(question.id)
            if answer and answer.is_correct:
                correct_count += 1
            question_answers.append((question, answer))
        
        total_questions = len(questions)
        
        return render_template('quiz_review.html',
                             session=session,
                             resource=resource,
                             question_answers=question_answers,
                             correct_count=correct_count,
                             total_questions=total_questions)
    except Exception as e:
        print(f"Error in quiz_review: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f'Error loading quiz review: {str(e)}', 'danger')
        return redirect(url_for('student_dashboard'))

@app.route('/api/track_activity', methods=['POST'])
@login_required
def track_activity():
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    data = request.get_json()
    
    try:
        # Create activity record
        activity = StudentActivity(
            student_id=student.id,
            resource_id=data.get('resource_id'),
            session_id=data.get('session_id'),
            activity_type=data.get('activity_type'),
            data=data.get('data', {})
        )
        
        db.session.add(activity)
        
        # Update engagement metrics
        engagement = ResourceEngagement.query.filter_by(
            student_id=student.id,
            resource_id=data.get('resource_id'),
            session_id=data.get('session_id')
        ).first()
        
        if not engagement:
            engagement = ResourceEngagement(
                student_id=student.id,
                resource_id=data.get('resource_id'),
                session_id=data.get('session_id')
            )
            db.session.add(engagement)
        
        # Update metrics based on activity type
        activity_type = data.get('activity_type')
        activity_data = data.get('data', {})
        
        if activity_type == 'scroll':
            scroll_percentage = activity_data.get('scroll_percentage', 0)
            engagement.scroll_depth = max(engagement.scroll_depth or 0, scroll_percentage)
        elif activity_type == 'cursor_move':
            engagement.cursor_movements = (engagement.cursor_movements or 0) + 1
        elif activity_type == 'click':
            engagement.clicks = (engagement.clicks or 0) + 1
        elif activity_type == 'focus_time':
            engagement.focus_time = (engagement.focus_time or 0) + activity_data.get('duration', 0)
        elif activity_type == 'idle_time':
            engagement.idle_time = (engagement.idle_time or 0) + activity_data.get('duration', 0)
        elif activity_type == 'time_spent':
            engagement.total_time_spent = (engagement.total_time_spent or 0) + activity_data.get('duration', 0)
        elif activity_type == 'page_hidden':
            engagement.distraction_count = (engagement.distraction_count or 0) + 1
        elif activity_type == 'page_visible':
            engagement.return_count = (engagement.return_count or 0) + 1
        elif activity_type == 'reading_speed':
            engagement.reading_speed = activity_data.get('wpm', 0)
        elif activity_type == 'comprehension_check':
            engagement.comprehension_score = activity_data.get('score', 0)
        elif activity_type == 'video_play':
            # Track video interactions
            engagement.clicks = (engagement.clicks or 0) + 1
        elif activity_type == 'video_pause':
            engagement.clicks = (engagement.clicks or 0) + 1
        elif activity_type == 'video_progress':
            # Update total time spent based on video progress
            progress_duration = activity_data.get('duration', 0)
            engagement.total_time_spent = (engagement.total_time_spent or 0) + progress_duration
        elif activity_type == 'video_complete':
            engagement.clicks = (engagement.clicks or 0) + 1
        elif activity_type == 'paste':
            # Paste events are stored in StudentActivity above; no aggregate metric yet
            pass
        elif activity_type == 'session_end':
            # Final session data - session-specific update (not cumulative)
            # Get the session to validate duration
            session = StudySession.query.get(data.get('session_id'))
            if session and session.start_time:
                # Calculate actual session duration
                actual_duration = int((datetime.now() - session.start_time).total_seconds())
                
                # Ensure metrics don't exceed session duration
                total_time_spent = min(activity_data.get('total_time_spent', 0), actual_duration)
                focus_time = min(activity_data.get('total_focus_time', 0), actual_duration)
                idle_time = min(activity_data.get('total_idle_time', 0), actual_duration)
                
                # Update engagement with session-specific data (not cumulative)
                engagement.total_time_spent = total_time_spent
                engagement.scroll_depth = activity_data.get('max_scroll_depth', 0)
                engagement.cursor_movements = activity_data.get('total_cursor_movements', 0)
                engagement.clicks = activity_data.get('total_clicks', 0)
                engagement.focus_time = focus_time
                engagement.idle_time = idle_time
                engagement.distraction_count = activity_data.get('distraction_count', 0)
                engagement.return_count = activity_data.get('return_count', 0)
            else:
                # Fallback to original data if session not found
                engagement.total_time_spent = activity_data.get('total_time_spent', 0)
                engagement.scroll_depth = activity_data.get('max_scroll_depth', 0)
                engagement.cursor_movements = activity_data.get('total_cursor_movements', 0)
                engagement.clicks = activity_data.get('total_clicks', 0)
                engagement.focus_time = activity_data.get('total_focus_time', 0)
                engagement.idle_time = activity_data.get('total_idle_time', 0)
                engagement.distraction_count = activity_data.get('distraction_count', 0)
                engagement.return_count = activity_data.get('return_count', 0)
        
        # Calculate enhanced engagement score
        engagement.engagement_score = calculate_engagement_score(engagement)
        
        # Update attention span
        if engagement.focus_time and engagement.focus_time > 0:
            engagement.attention_span = int(engagement.focus_time / max(engagement.distraction_count or 1, 1))
        
        engagement.last_updated = datetime.now()
        
        # Generate real-time predictions and notifications
        try:
            generate_success_prediction(student.id, data.get('resource_id'), data.get('session_id'))
            check_engagement_alerts(student.id, data.get('resource_id'), engagement)
        except Exception as e:
            print(f"Error generating predictions/alerts: {str(e)}")
        
        # Commit the changes
        db.session.commit()
        
        # Return comprehensive success data for debugging
        return jsonify({
            'success': True,
            'student_id': student.id,
            'resource_id': data.get('resource_id'),
            'session_id': data.get('session_id'),
            'activity_type': activity_type,
            'engagement_score': engagement.engagement_score
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error in track_activity: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

def generate_student_recommendation(engagement, resource_type):
    """Generate AI recommendation for students based on engagement data"""
    if not engagement:
        return "No recommendation available"
    
    total_time = engagement.total_time_spent or 0
    scroll_depth = engagement.scroll_depth or 0
    focus_time = engagement.focus_time or 0
    cursor_movements = engagement.cursor_movements or 0
    clicks = engagement.clicks or 0
    return_count = engagement.return_count or 0
    distraction_count = engagement.distraction_count or 0
    
    # Calculate engagement metrics
    focus_ratio = (focus_time / total_time * 100) if total_time > 0 else 0
    engagement_level = (scroll_depth + focus_ratio + (cursor_movements / 10) + (clicks * 5)) / 4
    
    if resource_type == 'video':
        if total_time < 60:
            return "Watch the complete video to fully understand the concepts. Take notes on key points."
        elif focus_ratio < 50:
            return "Minimize distractions while watching. Find a quiet environment and focus on the content."
        elif engagement_level > 80:
            return "Excellent engagement! Try pausing the video to reflect on key concepts and take detailed notes."
        else:
            return "Good progress! Try to be more active - pause to think, take notes, and ask yourself questions."
    
    elif resource_type == 'note':
        if total_time < 30:
            return "Spend more time reading the material. Read slowly and try to understand each section."
        elif scroll_depth < 50:
            return "Read through the entire content. Don't skip sections - every part is important."
        elif focus_ratio < 60:
            return "Improve your focus while reading. Find a quiet place and eliminate distractions."
        elif engagement_level > 75:
            return "Great reading! Try highlighting important points and summarizing in your own words."
        else:
            return "Good effort! Try reading more actively by asking questions and making connections."
    
    elif resource_type == 'link':
        if total_time < 15:
            return "Spend more time exploring the linked content. Click through different sections."
        elif clicks == 0:
            return "Interact with the linked content by clicking on different elements and exploring."
        elif engagement_level > 70:
            return "Excellent exploration! Consider bookmarking useful links for future reference."
        else:
            return "Good start! Try to explore more thoroughly and interact with the content."
    
    else:
        if engagement_level > 80:
            return "Excellent engagement! Keep up this level of active learning."
        elif engagement_level > 60:
            return "Good progress! Try to be even more active with the content."
        elif engagement_level > 40:
            return "Fair engagement. Try to spend more time and be more interactive with the material."
        else:
            return "Consider spending more time with this resource and being more actively engaged."

def generate_teacher_recommendation(engagement, resource_type, student_name):
    """Generate AI recommendation for teachers based on student engagement data"""
    if not engagement:
        return f"No engagement data available for {student_name}. Consider checking if they have accessed the resource."
    
    total_time = engagement.total_time_spent or 0
    scroll_depth = engagement.scroll_depth or 0
    focus_time = engagement.focus_time or 0
    cursor_movements = engagement.cursor_movements or 0
    clicks = engagement.clicks or 0
    return_count = engagement.return_count or 0
    distraction_count = engagement.distraction_count or 0
    
    # Calculate engagement metrics
    focus_ratio = (focus_time / total_time * 100) if total_time > 0 else 0
    engagement_level = (scroll_depth + focus_ratio + (cursor_movements / 10) + (clicks * 5)) / 4
    
    if resource_type == 'video':
        if total_time < 60:
            return f"Consider providing {student_name} with video summaries or guided notes to help them focus on key concepts."
        elif focus_ratio < 50:
            return f"Suggest a distraction-free environment for {student_name}. Consider shorter video segments or interactive elements."
        elif engagement_level > 80:
            return f"{student_name} shows excellent engagement! Consider providing advanced materials or discussion opportunities."
        else:
            return f"Encourage {student_name} to take notes while watching. Consider follow-up activities to reinforce learning."
    
    elif resource_type == 'note':
        if total_time < 30:
            return f"Provide {student_name} with reading guides or key questions to help them engage more deeply with the material."
        elif scroll_depth < 50:
            return f"Consider breaking down the content for {student_name} into smaller, more manageable sections."
        elif focus_ratio < 60:
            return f"Help {student_name} improve focus by suggesting study techniques or providing structured reading time."
        elif engagement_level > 75:
            return f"{student_name} shows great reading engagement! Consider providing extension materials or discussion topics."
        else:
            return f"Encourage {student_name} to read more actively. Consider providing reading strategies or guided questions."
    
    elif resource_type == 'link':
        if total_time < 15:
            return f"Provide {student_name} with specific tasks or questions to guide their exploration of linked content."
        elif clicks == 0:
            return f"Encourage {student_name} to interact with linked content. Consider providing a scavenger hunt or specific tasks."
        elif engagement_level > 70:
            return f"{student_name} shows excellent exploration skills! Consider providing more challenging linked resources."
        else:
            return f"Guide {student_name} to explore linked content more thoroughly. Provide specific learning objectives."
    
    else:
        if engagement_level > 80:
            return f"{student_name} shows excellent engagement! Consider providing advanced materials or leadership opportunities."
        elif engagement_level > 60:
            return f"Encourage {student_name} to maintain this level of engagement. Provide positive reinforcement."
        elif engagement_level > 40:
            return f"Support {student_name} in improving engagement. Consider one-on-one guidance or additional resources."
        else:
            return f"Provide {student_name} with additional support and guidance. Consider alternative learning approaches or resources."

def calculate_engagement_score(engagement):
    """Calculate overall engagement score based on multiple factors"""
    total_time = engagement.total_time_spent or 0
    if total_time == 0:
        return 0.0
    
    # Normalize factors (handle None values)
    scroll_depth = engagement.scroll_depth or 0
    focus_time = engagement.focus_time or 0
    cursor_movements = engagement.cursor_movements or 0
    clicks = engagement.clicks or 0
    return_count = engagement.return_count or 0
    distraction_count = engagement.distraction_count or 1
    
    scroll_score = min(scroll_depth / 100.0, 1.0) * 25  # 25% weight
    focus_score = min(focus_time / max(total_time, 1), 1.0) * 30  # 30% weight
    activity_score = min(cursor_movements / 100.0, 1.0) * 20  # 20% weight
    click_score = min(clicks / 50.0, 1.0) * 15  # 15% weight
    return_score = min(return_count / max(distraction_count, 1), 1.0) * 10  # 10% weight
    
    return min(scroll_score + focus_score + activity_score + click_score + return_score, 100.0)

def generate_success_prediction(student_id, resource_id, session_id):
    """Generate ML-based success prediction for student"""
    try:
        # Get engagement data
        engagement = ResourceEngagement.query.filter_by(
            student_id=student_id,
            resource_id=resource_id,
            session_id=session_id
        ).first()
        
        if not engagement:
            return
        
        # Prepare features for ML model
        features = {
            'total_time_spent': engagement.total_time_spent,
            'scroll_depth': engagement.scroll_depth,
            'cursor_movements': engagement.cursor_movements,
            'clicks': engagement.clicks,
            'focus_time': engagement.focus_time,
            'idle_time': engagement.idle_time,
            'engagement_score': engagement.engagement_score,
            'reading_speed': engagement.reading_speed,
            'comprehension_score': engagement.comprehension_score,
            'attention_span': engagement.attention_span,
            'distraction_count': engagement.distraction_count,
            'return_count': engagement.return_count
        }
        
        # Get ML prediction
        from ml_service import predict_success_enhanced
        prediction = predict_success_enhanced(features)
        
        # Store prediction
        success_prediction = StudentSuccessPrediction(
            student_id=student_id,
            resource_id=resource_id,
            session_id=session_id,
            predicted_score=prediction.get('predicted_score', 0),
            success_probability=prediction.get('success_probability', 0),
            confidence_level=prediction.get('confidence_level', 0),
            prediction_factors=prediction.get('factors', {})
        )
        
        db.session.add(success_prediction)
        db.session.commit()
        
    except Exception as e:
        print(f"Error generating success prediction: {str(e)}")

def check_engagement_alerts(student_id, resource_id, engagement):
    """Check for engagement issues and create teacher notifications"""
    try:
        student = Student.query.get(student_id)
        if not student:
            return
        
        # Check for low engagement
        if engagement.engagement_score < 30 and engagement.total_time_spent > 300:  # 5 minutes
            create_teacher_notification(
                teacher_id=student.teacher_id,
                student_id=student_id,
                resource_id=resource_id,
                notification_type='low_engagement',
                title='Low Student Engagement Detected',
                message=f'{student.name} shows low engagement (score: {engagement.engagement_score:.1f}) on resource. Consider providing additional support.',
                severity='warning'
            )
        
        # Check for excessive idle time
        if engagement.idle_time > engagement.focus_time * 0.5:  # More than 50% idle
            create_teacher_notification(
                teacher_id=student.teacher_id,
                student_id=student_id,
                resource_id=resource_id,
                notification_type='idle_alert',
                title='Student Inactivity Alert',
                message=f'{student.name} has been inactive for {engagement.idle_time//60} minutes. They may need assistance.',
                severity='alert'
            )
        
        # Check for frequent distractions
        if engagement.distraction_count > 5:
            create_teacher_notification(
                teacher_id=student.teacher_id,
                student_id=student_id,
                resource_id=resource_id,
                notification_type='distraction_alert',
                title='Frequent Distractions Detected',
                message=f'{student.name} has left the page {engagement.distraction_count} times. Consider checking in.',
                severity='warning'
            )
            
    except Exception as e:
        print(f"Error checking engagement alerts: {str(e)}")

def create_teacher_notification(teacher_id, student_id, resource_id, notification_type, title, message, severity='info'):
    """Create a notification for the teacher"""
    try:
        # Check if similar notification already exists (within last 30 minutes)
        recent_notification = TeacherNotification.query.filter_by(
            teacher_id=teacher_id,
            student_id=student_id,
            resource_id=resource_id,
            notification_type=notification_type,
            is_read=False
        ).filter(
            TeacherNotification.created_at >= datetime.now() - timedelta(minutes=30)
        ).first()
        
        if recent_notification:
            return  # Avoid duplicate notifications
        
        notification = TeacherNotification(
            teacher_id=teacher_id,
            student_id=student_id,
            resource_id=resource_id,
            notification_type=notification_type,
            title=title,
            message=message,
            severity=severity
        )
        
        db.session.add(notification)
        db.session.commit()
        
    except Exception as e:
        print(f"Error creating teacher notification: {str(e)}"), 500

@app.route('/teacher/analytics')
@login_required
def student_analytics():
    if current_user.role != 'teacher':
        abort(403)
    
    # Get teacher's students and resources
    students = Student.query.filter_by(teacher_id=current_user.id).all()
    resources = Resource.query.filter_by(created_by=current_user.id).all()
    
    # Get engagement data
    engagements = db.session.query(
        ResourceEngagement,
        Resource.title.label('resource_title'),
        Resource.resource_type,
        Student.name.label('student_name')
    ).join(Resource, ResourceEngagement.resource_id == Resource.id)\
     .join(Student, ResourceEngagement.student_id == Student.id)\
     .filter(Resource.created_by == current_user.id)\
     .order_by(ResourceEngagement.last_updated.desc()).all()
    
    # Convert Row objects to dictionaries for template
    engagement_data = []
    for row in engagements:
        engagement_data.append({
            'total_time_spent': row.ResourceEngagement.total_time_spent or 0,
            'scroll_depth': row.ResourceEngagement.scroll_depth or 0,
            'cursor_movements': row.ResourceEngagement.cursor_movements or 0,
            'clicks': row.ResourceEngagement.clicks or 0,
            'focus_time': row.ResourceEngagement.focus_time or 0,
            'last_updated': row.ResourceEngagement.last_updated,
            'resource_title': row.resource_title,
            'resource_type': row.resource_type,
            'student_name': row.student_name
        })
    
    # Get recent activities
    recent_activities_raw = db.session.query(
        StudentActivity,
        Resource.title.label('resource_title'),
        Student.name.label('student_name')
    ).join(Resource, StudentActivity.resource_id == Resource.id)\
     .join(Student, StudentActivity.student_id == Student.id)\
     .filter(Resource.created_by == current_user.id)\
     .order_by(StudentActivity.timestamp.desc()).limit(50).all()
    
    # Convert Row objects to dictionaries for template
    recent_activities = []
    for row in recent_activities_raw:
        recent_activities.append({
            'activity_type': row.StudentActivity.activity_type,
            'timestamp': row.StudentActivity.timestamp,
            'data': row.StudentActivity.data,
            'resource_title': row.resource_title,
            'student_name': row.student_name
        })
    
    # Calculate summary stats
    total_students = len(students)
    total_resources = len(resources)
    
    if engagement_data:
        avg_engagement_time = sum([e['total_time_spent'] for e in engagement_data]) / len(engagement_data) / 60
        avg_scroll_depth = sum([e['scroll_depth'] for e in engagement_data]) / len(engagement_data)
    else:
        avg_engagement_time = 0
        avg_scroll_depth = 0
    
    return render_template('student_analytics.html',
                         engagements=engagement_data,
                         recent_activities=recent_activities,
                         total_students=total_students,
                         total_resources=total_resources,
                         avg_engagement_time=avg_engagement_time,
                         avg_scroll_depth=avg_scroll_depth)

@app.route('/teacher/real_time_tracking')
@login_required
@teacher_required
def real_time_tracking():
    """Real-time student activity tracking dashboard"""
    students = Student.query.filter_by(teacher_id=current_user.id).all()
    student_ids = [s.id for s in students]
    
    # Get active sessions (sessions started in last 2 hours)
    active_sessions = StudySession.query.filter(
        StudySession.student_id.in_(student_ids),
        StudySession.start_time >= datetime.now() - timedelta(hours=2),
        StudySession.completed == False
    ).all()
    
    # Get recent engagement data (last 2 hours for better coverage)
    recent_engagement = ResourceEngagement.query.filter(
        ResourceEngagement.student_id.in_(student_ids),
        ResourceEngagement.last_updated >= datetime.now() - timedelta(hours=2)
    ).order_by(ResourceEngagement.last_updated.desc()).limit(20).all()
    
    # Get recent predictions
    recent_predictions = StudentSuccessPrediction.query.filter(
        StudentSuccessPrediction.student_id.in_(student_ids),
        StudentSuccessPrediction.created_at >= datetime.now() - timedelta(hours=2)
    ).order_by(StudentSuccessPrediction.created_at.desc()).limit(10).all()
    
    # Get current active students (students with engagement in last 30 minutes)
    active_students = ResourceEngagement.query.filter(
        ResourceEngagement.student_id.in_(student_ids),
        ResourceEngagement.last_updated >= datetime.now() - timedelta(minutes=30)
    ).distinct(ResourceEngagement.student_id).all()
    
    # Get real-time activity data (last 10 minutes)
    recent_activities = StudentActivity.query.filter(
        StudentActivity.student_id.in_(student_ids),
        StudentActivity.timestamp >= datetime.now() - timedelta(minutes=10)
    ).order_by(StudentActivity.timestamp.desc()).limit(50).all()
    
    # Get all resources for display
    resource_ids = set()
    for engagement in recent_engagement:
        if engagement.resource_id:
            resource_ids.add(engagement.resource_id)
    for session in active_sessions:
        if session.resource_id:
            resource_ids.add(session.resource_id)
    
    resources = Resource.query.filter(Resource.id.in_(list(resource_ids))).all() if resource_ids else []
    
    return render_template('real_time_tracking.html',
                         students=students,
                         active_sessions=active_sessions,
                         recent_engagement=recent_engagement,
                         recent_predictions=recent_predictions,
                         active_students=active_students,
                         recent_activities=recent_activities,
                         resources=resources)

@app.route('/api/teacher/notifications')
@login_required
@teacher_required
def get_teacher_notifications():
    """Get unread notifications for teacher"""
    notifications = TeacherNotification.query.filter_by(
        teacher_id=current_user.id,
        is_read=False
    ).order_by(TeacherNotification.created_at.desc()).limit(10).all()
    
    return jsonify([{
        'id': n.id,
        'title': n.title,
        'message': n.message,
        'severity': n.severity,
        'created_at': n.created_at.isoformat(),
        'student_id': n.student_id,
        'resource_id': n.resource_id,
        'notification_type': n.notification_type
    } for n in notifications])

@app.route('/api/teacher/mark_notification_read/<int:notification_id>', methods=['POST'])
@login_required
@teacher_required
def mark_notification_read(notification_id):
    """Mark a notification as read"""
    notification = TeacherNotification.query.filter_by(
        id=notification_id,
        teacher_id=current_user.id
    ).first()
    
    if notification:
        notification.is_read = True
        db.session.commit()
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'error': 'Notification not found'})

@app.route('/api/teacher/student_activity/all')
@login_required
@teacher_required
def get_all_student_activity():
    """Get all student activity data for teacher's students"""
    students = Student.query.filter_by(teacher_id=current_user.id).all()
    student_ids = [s.id for s in students]
    
    # Get recent engagement data (using local time)
    engagement_data = ResourceEngagement.query.filter(
        ResourceEngagement.student_id.in_(student_ids),
        ResourceEngagement.last_updated >= datetime.now() - timedelta(hours=2)
    ).order_by(ResourceEngagement.last_updated.desc()).limit(20).all()
    
    # Get recent predictions
    predictions = StudentSuccessPrediction.query.filter(
        StudentSuccessPrediction.student_id.in_(student_ids),
        StudentSuccessPrediction.created_at >= datetime.now() - timedelta(hours=2)
    ).order_by(StudentSuccessPrediction.created_at.desc()).limit(10).all()
    
    # Format engagement data
    engagement_list = []
    for engagement in engagement_data:
        student = next((s for s in students if s.id == engagement.student_id), None)
        resource = Resource.query.get(engagement.resource_id) if engagement.resource_id else None
        
        # Compute session-scoped time spent to avoid showing cumulative lifetime time
        session_time_spent = None
        try:
            latest_session = StudySession.query.filter_by(
                student_id=engagement.student_id,
                resource_id=engagement.resource_id
            ).order_by(StudySession.start_time.desc()).first()
            if latest_session:
                if latest_session.completed:
                    # Use recorded duration if available; else compute from timestamps
                    if latest_session.duration is not None:
                        session_time_spent = int(latest_session.duration)
                    elif latest_session.end_time and latest_session.start_time:
                        session_time_spent = int((latest_session.end_time - latest_session.start_time).total_seconds())
                else:
                    # Ongoing session: time since start
                    if latest_session.start_time:
                        session_time_spent = int((datetime.now() - latest_session.start_time).total_seconds())
        except Exception:
            session_time_spent = None

        engagement_list.append({
            'student_name': student.name if student else 'Unknown',
            'resource_title': resource.title if resource else 'Unknown',
            'engagement_score': engagement.engagement_score or 0,
            'total_time_spent': engagement.total_time_spent or 0,
            'session_time_spent': session_time_spent,
            'scroll_depth': engagement.scroll_depth or 0,
            'focus_time': engagement.focus_time or 0,
            'cursor_movements': engagement.cursor_movements or 0,
            'clicks': engagement.clicks or 0,
            'idle_time': engagement.idle_time or 0,
            'distraction_count': engagement.distraction_count or 0,
            'return_count': engagement.return_count or 0,
            'last_updated': engagement.last_updated.strftime('%Y-%m-%d %H:%M:%S') if engagement.last_updated else None
        })
    
    # Format predictions data
    predictions_list = []
    for prediction in predictions:
        student = next((s for s in students if s.id == prediction.student_id), None)
        
        predictions_list.append({
            'student_name': student.name if student else 'Unknown',
            'predicted_score': prediction.predicted_score or 0,
            'success_probability': prediction.success_probability or 0,
            'confidence_level': prediction.confidence_level or 0,
            'created_at': prediction.created_at.isoformat() if prediction.created_at else None
        })
    
    return jsonify({
        'engagement': engagement_list,
        'predictions': predictions_list
    })

@app.route('/api/teacher/student_activity/<int:student_id>')
@login_required
@teacher_required
def get_student_activity(student_id):
    """Get detailed activity data for a specific student"""
    # Verify teacher has access to this student
    student = Student.query.filter_by(id=student_id, teacher_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get recent engagement data
    engagement_data = ResourceEngagement.query.filter_by(
        student_id=student_id
    ).order_by(ResourceEngagement.last_updated.desc()).limit(20).all()
    
    # Get recent predictions
    predictions = StudentSuccessPrediction.query.filter_by(
        student_id=student_id
    ).order_by(StudentSuccessPrediction.created_at.desc()).limit(10).all()
    
    # Get recent activities
    activities = StudentActivity.query.filter_by(
        student_id=student_id
    ).order_by(StudentActivity.timestamp.desc()).limit(50).all()
    
    return jsonify({
        'student': {
            'id': student.id,
            'name': student.name,
            'student_id': student.student_id,
            'grade': student.grade
        },
        'engagement': [{
            'resource_id': e.resource_id,
            'resource_title': Resource.query.get(e.resource_id).title if Resource.query.get(e.resource_id) else 'Unknown Resource',
            'student_name': student.name,
            'total_time_spent': e.total_time_spent or 0,
            'session_time_spent': e.session_time_spent or 0,
            'engagement_score': e.engagement_score or 0,
            'scroll_depth': e.scroll_depth or 0,
            'focus_time': e.focus_time or 0,
            'idle_time': e.idle_time or 0,
            'cursor_movements': e.cursor_movements or 0,
            'clicks': e.clicks or 0,
            'distraction_count': e.distraction_count or 0,
            'return_count': e.return_count or 0,
            'last_updated': e.last_updated.isoformat() if e.last_updated else datetime.now().isoformat()
        } for e in engagement_data],
        'predictions': [{
            'resource_id': p.resource_id,
            'predicted_score': p.predicted_score,
            'success_probability': p.success_probability,
            'confidence_level': p.confidence_level,
            'created_at': p.created_at.isoformat()
        } for p in predictions],
        'activities': [{
            'activity_type': a.activity_type,
            'timestamp': a.timestamp.isoformat(),
            'data': a.data
        } for a in activities]
    })

@app.route('/teacher/student_detailed_report/<int:student_id>')
@login_required
@teacher_required
def student_detailed_report(student_id):
    """Detailed student performance and activity report"""
    # Verify teacher has access to this student
    student = Student.query.filter_by(id=student_id, teacher_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get all study sessions
    sessions = StudySession.query.filter_by(student_id=student_id).all()
    
    # Get all engagement data
    engagement_data = ResourceEngagement.query.filter_by(student_id=student_id).all()
    
    # Get all predictions
    predictions = StudentSuccessPrediction.query.filter_by(student_id=student_id).all()
    
    # Get learning profile
    learning_profile = StudentLearningProfile.query.filter_by(student_id=student_id).first()
    
    # Calculate summary statistics
    total_sessions = len(sessions)
    completed_sessions = len([s for s in sessions if s.completed])
    avg_score = sum([s.quiz_score or 0 for s in sessions]) / len(sessions) if sessions else 0
    avg_engagement = sum([e.engagement_score for e in engagement_data]) / len(engagement_data) if engagement_data else 0
    
    # Get resources for display
    resource_ids = list(set([s.resource_id for s in sessions] + [e.resource_id for e in engagement_data] + [p.resource_id for p in predictions]))
    resources = Resource.query.filter(Resource.id.in_(resource_ids)).all() if resource_ids else []
    
    # Get activities for timeline - get ALL activities for comprehensive reporting
    activities = StudentActivity.query.filter_by(student_id=student_id).order_by(StudentActivity.timestamp.desc()).all()
    
    # Calculate engagement statistics
    if engagement_data:
        engagement_stats = {
            'avg_time_spent': sum([e.total_time_spent for e in engagement_data]) / len(engagement_data),
            'avg_scroll_depth': sum([e.scroll_depth for e in engagement_data]) / len(engagement_data),
            'avg_clicks': sum([e.clicks for e in engagement_data]) / len(engagement_data),
            'avg_cursor_moves': sum([e.cursor_movements for e in engagement_data]) / len(engagement_data),
            'avg_focus_time': sum([e.focus_time for e in engagement_data]) / len(engagement_data),
            'avg_engagement_score': sum([e.engagement_score for e in engagement_data]) / len(engagement_data),
            'avg_distraction_count': sum([e.distraction_count for e in engagement_data]) / len(engagement_data),
            'total_activities': len(activities),
            'activity_types': {}
        }
        
        # Calculate activity type frequency
        for activity in activities:
            activity_type = activity.activity_type
            if activity_type in engagement_stats['activity_types']:
                engagement_stats['activity_types'][activity_type] += 1
            else:
                engagement_stats['activity_types'][activity_type] = 1
    else:
        engagement_stats = {
            'avg_time_spent': 0,
            'avg_scroll_depth': 0,
            'avg_clicks': 0,
            'avg_cursor_moves': 0,
            'avg_focus_time': 0,
            'avg_engagement_score': 0,
            'avg_distraction_count': 0,
            'total_activities': len(activities),
            'activity_types': {}
        }
    
    # Serialize objects for JSON usage in template (keep original objects for Jinja rendering)
    student_json = {
        'id': student.id,
        'name': student.name,
        'student_id': student.student_id,
        'grade': student.grade,
        'created_at': student.created_at.isoformat() if hasattr(student, 'created_at') and student.created_at else None,
    }

    sessions_json = [{
        'id': s.id,
        'resource_id': s.resource_id,
        'start_time': s.start_time.isoformat() if s.start_time else None,
        'end_time': s.end_time.isoformat() if s.end_time else None,
        'duration': s.duration,
        'quiz_score': s.quiz_score,
        'completed': s.completed,
        'ai_recommendation': s.ai_recommendation,
    } for s in sessions]

    engagement_data_json = [{
        'id': e.id,
        'resource_id': e.resource_id,
        'session_id': e.session_id,
        'total_time_spent': e.total_time_spent,
        'scroll_depth': e.scroll_depth,
        'cursor_movements': e.cursor_movements,
        'clicks': e.clicks,
        'focus_time': e.focus_time,
        'idle_time': e.idle_time,
        'last_updated': e.last_updated.isoformat() if e.last_updated else None,
        'reading_speed': e.reading_speed,
        'comprehension_score': e.comprehension_score,
        'engagement_score': e.engagement_score,
        'attention_span': e.attention_span,
        'distraction_count': e.distraction_count,
        'return_count': e.return_count,
    } for e in engagement_data]

    predictions_json = [{
        'resource_id': p.resource_id,
        'predicted_score': p.predicted_score,
        'success_probability': p.success_probability,
        'confidence_level': p.confidence_level,
        'created_at': p.created_at.isoformat() if p.created_at else None,
    } for p in predictions]

    learning_profile_json = None
    if learning_profile:
        learning_profile_json = {
            'student_id': learning_profile.student_id,
            'learning_style': learning_profile.learning_style,
            'attention_span_avg': learning_profile.attention_span_avg,
            'preferred_session_duration': learning_profile.preferred_session_duration,
            'engagement_pattern': learning_profile.engagement_pattern,
            'success_factors': learning_profile.success_factors,
            'last_updated': learning_profile.last_updated.isoformat() if learning_profile.last_updated else None,
        }

    resources_json = [{
        'id': r.id,
        'title': r.title,
        'resource_type': r.resource_type,
        'grade': r.grade,
    } for r in resources]

    activities_json = [{
        'activity_type': a.activity_type,
        'timestamp': a.timestamp.isoformat() if a.timestamp else None,
        'data': a.data,
        'resource_id': a.resource_id,
        'session_id': a.session_id,
    } for a in activities]

    return render_template('student_detailed_report.html',
                         student=student,
                         student_json=student_json,
                         sessions_json=sessions_json,
                         engagement_data_json=engagement_data_json,
                         predictions_json=predictions_json,
                         learning_profile_json=learning_profile_json,
                         resources_json=resources_json,
                         activities_json=activities_json,
                         total_sessions=total_sessions,
                         completed_sessions=completed_sessions,
                         avg_score=avg_score,
                         avg_engagement=avg_engagement,
                         engagement_stats=engagement_stats)

@app.route('/teacher/student_printable_report/<int:student_id>')
@login_required
@teacher_required
def student_printable_report(student_id):
    """Printable student engagement report"""
    # Verify teacher has access to this student
    student = Student.query.filter_by(id=student_id, teacher_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get teacher name
    teacher = User.query.get(current_user.id)
    teacher_name = teacher.username if teacher else "Unknown"
    
    # Get all study sessions with engagement data
    sessions = StudySession.query.filter_by(student_id=student_id).all()
    engagement_data = ResourceEngagement.query.filter_by(student_id=student_id).all()
    
    # Create engagement sessions data for the table
    engagement_sessions = []
    for session in sessions:
        resource = Resource.query.get(session.resource_id)
        if not resource:
            continue
            
        # Get engagement data for this session
        engagement = ResourceEngagement.query.filter_by(
            student_id=student_id, 
            resource_id=session.resource_id
        ).first()
        
        # Calculate time spent (use total_seconds to include >1h durations accurately)
        if session.end_time and session.start_time:
            duration_seconds = int((session.end_time - session.start_time).total_seconds())
            time_spent = f"{duration_seconds // 60}m {duration_seconds % 60}s"
        else:
            time_spent = "0m 0s"
        
        # Get engagement metrics
        scroll_depth = engagement.scroll_depth if engagement else 0
        cursor_moves = engagement.cursor_movements if engagement else 0
        clicks = engagement.clicks if engagement else 0
        focus_time = engagement.focus_time if engagement else 0
        engagement_score = engagement.engagement_score if engagement else 0
        
        # Format focus time
        if focus_time:
            focus_minutes = focus_time // 60
            focus_seconds = focus_time % 60
            focus_time_str = f"{focus_minutes}m {focus_seconds}s"
        else:
            focus_time_str = "0m 0s"
        
        # Format last activity
        last_activity = session.start_time.strftime("%m/%d %H:%M") if session.start_time else "N/A"
        
        engagement_sessions.append({
            'resource_title': resource.title,
            'time_spent': time_spent,
            'scroll_depth': f"{scroll_depth:.1f}",
            'cursor_moves': cursor_moves,
            'clicks': clicks,
            'focus_time': focus_time_str,
            'engagement_score': f"{engagement_score:.0f}",
            'engagement_score_numeric': engagement_score,
            'last_activity': last_activity
        })
    
    # Calculate summary statistics
    total_sessions = len(sessions)
    completed_sessions = len([s for s in sessions if s.completed])
    avg_score = sum([s.quiz_score or 0 for s in sessions]) / len(sessions) if sessions else 0
    avg_engagement = sum([e.engagement_score for e in engagement_data]) / len(engagement_data) if engagement_data else 0
    
    # Calculate engagement levels
    high_engagement_count = len([e for e in engagement_data if e.engagement_score >= 80])
    medium_engagement_count = len([e for e in engagement_data if 50 <= e.engagement_score < 80])
    low_engagement_count = len([e for e in engagement_data if e.engagement_score < 50])
    
    # Calculate average session duration
    total_duration = 0
    valid_sessions = 0
    for session in sessions:
        if session.end_time and session.start_time:
            duration = (session.end_time - session.start_time).total_seconds()
            total_duration += duration
            valid_sessions += 1
    
    avg_duration_minutes = (total_duration / valid_sessions / 60) if valid_sessions > 0 else 0
    avg_session_duration = f"{avg_duration_minutes:.1f} minutes"
    
    # Calculate focus efficiency
    total_focus_time = sum([e.focus_time for e in engagement_data if e.focus_time])
    total_time_spent = sum([(s.end_time - s.start_time).total_seconds() for s in sessions if s.end_time and s.start_time])
    focus_efficiency = (total_focus_time / total_time_spent * 100) if total_time_spent > 0 else 0
    
    # Generate recommendations
    recommendations = []
    if avg_engagement < 50:
        recommendations.append("Student shows low engagement. Consider more interactive content and shorter sessions.")
    elif avg_engagement < 80:
        recommendations.append("Good engagement level. Continue with current approach and add variety to content.")
    else:
        recommendations.append("Excellent engagement! Student is highly motivated. Consider advanced materials.")
    
    if focus_efficiency < 30:
        recommendations.append("Low focus efficiency. Consider breaking content into smaller chunks with breaks.")
    elif focus_efficiency > 70:
        recommendations.append("High focus efficiency. Student maintains good concentration throughout sessions.")
    
    if total_sessions > 0 and completed_sessions / total_sessions < 0.7:
        recommendations.append("Low completion rate. Consider adjusting difficulty level or providing more support.")
    
    # Get most active time (simplified)
    most_active_time = "Morning (9-12 AM)"  # This could be calculated from actual data
    
    return render_template('student_printable_report.html',
                         student=student,
                         teacher_name=teacher_name,
                         report_date=datetime.now().strftime("%B %d, %Y"),
                         engagement_sessions=engagement_sessions,
                         total_sessions=total_sessions,
                         completed_sessions=completed_sessions,
                         avg_score=avg_score,
                         avg_engagement=avg_engagement,
                         high_engagement_count=high_engagement_count,
                         medium_engagement_count=medium_engagement_count,
                         low_engagement_count=low_engagement_count,
                         avg_session_duration=avg_session_duration,
                         most_active_time=most_active_time,
                         focus_efficiency=f"{focus_efficiency:.1f}",
                         recommendations=recommendations)

@app.route('/api/teacher/session_details/<int:session_id>')
@login_required
@teacher_required
def get_session_details(session_id):
    """Get detailed session information"""
    try:
        session = StudySession.query.get_or_404(session_id)
        
        # Verify teacher has access to this session
        student = Student.query.get_or_404(session.student_id)
        if student.teacher_id != current_user.id:
            abort(403)
        
        # Get engagement data for this session
        engagement = ResourceEngagement.query.filter_by(
            session_id=session_id
        ).first()
        
        # Get resource information
        resource = Resource.query.get(session.resource_id)
        
        # Debug logging
        print(f"Session {session_id} - Start time: {session.start_time}, End time: {session.end_time}")
        print(f"Engagement data: {engagement}")
        
        return jsonify({
            'session': {
                'id': session.id,
                'start_time': session.start_time.isoformat() if session.start_time else None,
                'end_time': session.end_time.isoformat() if session.end_time else None,
                'duration': session.duration,
                'quiz_score': session.quiz_score,
                'completed': session.completed
            },
            'engagement': {
                'total_time_spent': engagement.total_time_spent if engagement else 0,
                'engagement_score': engagement.engagement_score if engagement else 0,
                'scroll_depth': engagement.scroll_depth if engagement else 0,
                'focus_time': engagement.focus_time if engagement else 0,
                'clicks': engagement.clicks if engagement else 0,
                'cursor_movements': engagement.cursor_movements if engagement else 0
            },
            'resource': {
                'title': resource.title if resource else 'Unknown',
                'resource_type': resource.resource_type if resource else 'unknown'
            }
        })
        
    except Exception as e:
        print(f"Error in get_session_details: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/teacher/ml_analytics')
@login_required
@teacher_required
def get_ml_analytics():
    """Get ML-enhanced analytics data"""
    try:
        # Get all engagement data for ML analysis
        students = Student.query.filter_by(teacher_id=current_user.id).all()
        student_ids = [s.id for s in students]
        
        engagements = db.session.query(ResourceEngagement).filter(
            ResourceEngagement.student_id.in_(student_ids),
            ResourceEngagement.last_updated >= datetime.now() - timedelta(days=7)
        ).all()
        
        if not engagements:
            return jsonify({
                'avg_focus_percentage': 0,
                'avg_scroll_percentage': 0,
                'avg_click_percentage': 0,
                'avg_cursor_percentage': 0,
                'avg_return_percentage': 0,
                'avg_reading_percentage': 0,
                'focus_percentage': 0,
                'distraction_percentage': 0,
                'activity_percentage': 0,
                'idle_percentage': 0,
                'time_labels': ['0-5m', '5-10m', '10-15m', '15-20m', '20-25m', '25-30m'],
                'engagement_over_time': [0, 0, 0, 0, 0, 0],
                'low_risk_percentage': 0,
                'medium_risk_percentage': 0,
                'high_risk_percentage': 0,
                'insights': []
            })
        
        # Calculate ML-enhanced percentages
        total_engagements = len(engagements)
        
        # Focus time analysis
        total_focus_time = sum(e.focus_time or 0 for e in engagements)
        total_time_spent = sum(e.total_time_spent or 0 for e in engagements)
        avg_focus_percentage = (total_focus_time / max(total_time_spent, 1)) * 100 if total_time_spent > 0 else 0
        
        # Scroll depth analysis
        avg_scroll_percentage = sum(e.scroll_depth or 0 for e in engagements) / total_engagements
        
        # Click activity analysis (normalized to percentage)
        total_clicks = sum(e.clicks or 0 for e in engagements)
        max_clicks = max(e.clicks or 0 for e in engagements) if engagements else 1
        avg_click_percentage = (total_clicks / max(max_clicks * total_engagements, 1)) * 100
        
        # Cursor movement analysis
        total_cursor_movements = sum(e.cursor_movements or 0 for e in engagements)
        max_cursor = max(e.cursor_movements or 0 for e in engagements) if engagements else 1
        avg_cursor_percentage = (total_cursor_movements / max(max_cursor * total_engagements, 1)) * 100
        
        # Return rate analysis
        total_returns = sum(e.return_count or 0 for e in engagements)
        total_distractions = sum(e.distraction_count or 0 for e in engagements)
        avg_return_percentage = (total_returns / max(total_distractions, 1)) * 100
        
        # Reading speed analysis
        reading_speeds = [e.reading_speed or 0 for e in engagements if e.reading_speed]
        avg_reading_percentage = (sum(reading_speeds) / max(len(reading_speeds), 1)) / 200 * 100  # Normalize to 200 WPM max
        
        # Behavior distribution
        focused_count = sum(1 for e in engagements if (e.focus_time or 0) > (e.total_time_spent or 1) * 0.7)
        distracted_count = sum(1 for e in engagements if (e.distraction_count or 0) > 3)
        active_count = sum(1 for e in engagements if (e.cursor_movements or 0) > 50)
        idle_count = sum(1 for e in engagements if (e.idle_time or 0) > (e.total_time_spent or 1) * 0.5)
        
        focus_percentage = (focused_count / total_engagements) * 100
        distraction_percentage = (distracted_count / total_engagements) * 100
        activity_percentage = (active_count / total_engagements) * 100
        idle_percentage = (idle_count / total_engagements) * 100
        
        # Time-based engagement analysis
        time_labels = ['0-5m', '5-10m', '10-15m', '15-20m', '20-25m', '25-30m']
        engagement_over_time = []
        
        for i, time_range in enumerate([(0, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 30)]):
            time_engagements = [e for e in engagements 
                              if time_range[0] <= (e.total_time_spent or 0) / 60 < time_range[1]]
            if time_engagements:
                avg_engagement = sum(e.engagement_score or 0 for e in time_engagements) / len(time_engagements)
                engagement_over_time.append(avg_engagement)
            else:
                engagement_over_time.append(0)
        
        # Success risk assessment
        low_risk = sum(1 for e in engagements if (e.engagement_score or 0) >= 80)
        medium_risk = sum(1 for e in engagements if 60 <= (e.engagement_score or 0) < 80)
        high_risk = sum(1 for e in engagements if (e.engagement_score or 0) < 60)
        
        low_risk_percentage = (low_risk / total_engagements) * 100
        medium_risk_percentage = (medium_risk / total_engagements) * 100
        high_risk_percentage = (high_risk / total_engagements) * 100
        
        # Generate ML insights
        insights = generate_ml_insights(engagements, {
            'avg_focus_percentage': avg_focus_percentage,
            'avg_scroll_percentage': avg_scroll_percentage,
            'focus_percentage': focus_percentage,
            'distraction_percentage': distraction_percentage,
            'low_risk_percentage': low_risk_percentage,
            'high_risk_percentage': high_risk_percentage
        })
        
        return jsonify({
            'avg_focus_percentage': round(avg_focus_percentage, 1),
            'avg_scroll_percentage': round(avg_scroll_percentage, 1),
            'avg_click_percentage': round(avg_click_percentage, 1),
            'avg_cursor_percentage': round(avg_cursor_percentage, 1),
            'avg_return_percentage': round(avg_return_percentage, 1),
            'avg_reading_percentage': round(avg_reading_percentage, 1),
            'focus_percentage': round(focus_percentage, 1),
            'distraction_percentage': round(distraction_percentage, 1),
            'activity_percentage': round(activity_percentage, 1),
            'idle_percentage': round(idle_percentage, 1),
            'time_labels': time_labels,
            'engagement_over_time': [round(x, 1) for x in engagement_over_time],
            'low_risk_percentage': round(low_risk_percentage, 1),
            'medium_risk_percentage': round(medium_risk_percentage, 1),
            'high_risk_percentage': round(high_risk_percentage, 1),
            'insights': insights
        })
        
    except Exception as e:
        print(f"Error generating ML analytics: {str(e)}")
        return jsonify({'error': 'Failed to generate ML analytics'}), 500

def generate_ml_insights(engagements, metrics):
    """Generate AI-powered insights based on engagement data"""
    insights = []
    
    # Focus analysis
    if metrics['avg_focus_percentage'] < 50:
        insights.append({
            'title': 'Low Focus Alert',
            'description': f"Students are only focused {metrics['avg_focus_percentage']:.1f}% of the time. Consider implementing shorter sessions or more interactive content."
        })
    elif metrics['avg_focus_percentage'] > 80:
        insights.append({
            'title': 'Excellent Focus',
            'description': f"Students are highly focused ({metrics['avg_focus_percentage']:.1f}%). The current content format is working well."
        })
    
    # Distraction analysis
    if metrics['distraction_percentage'] > 30:
        insights.append({
            'title': 'High Distraction Rate',
            'description': f"{metrics['distraction_percentage']:.1f}% of sessions show high distraction. Consider adding engagement breaks or interactive elements."
        })
    
    # Success risk analysis
    if metrics['high_risk_percentage'] > 40:
        insights.append({
            'title': 'Success Risk Warning',
            'description': f"{metrics['high_risk_percentage']:.1f}% of students are at high risk. Immediate intervention recommended."
        })
    elif metrics['low_risk_percentage'] > 60:
        insights.append({
            'title': 'Strong Performance',
            'description': f"{metrics['low_risk_percentage']:.1f}% of students are performing well. Consider advanced content for these students."
        })
    
    # Scroll depth analysis
    avg_scroll = metrics['avg_scroll_percentage']
    if avg_scroll < 30:
        insights.append({
            'title': 'Low Content Engagement',
            'description': f"Students are only scrolling through {avg_scroll:.1f}% of content. Content may be too long or not engaging enough."
        })
    
    # Default insight if no specific patterns detected
    if not insights:
        insights.append({
            'title': 'Stable Performance',
            'description': "Student engagement patterns are within normal ranges. Continue monitoring for any significant changes."
        })
    
    return insights

@app.route('/api/teacher/active_sessions')
@login_required
@teacher_required
def get_active_sessions():
    """Get currently active study sessions"""
    students = Student.query.filter_by(teacher_id=current_user.id).all()
    student_ids = [s.id for s in students]
    
    # Get active study sessions
    active_sessions = db.session.query(
        StudySession, Student, Resource
    ).join(
        Student, StudySession.student_id == Student.id
    ).join(
        Resource, StudySession.resource_id == Resource.id
    ).filter(
        StudySession.student_id.in_(student_ids),
        StudySession.completed == False,
        StudySession.start_time >= datetime.now() - timedelta(hours=2)
    ).all()
    
    sessions = []
    for session, student, resource in active_sessions:
        duration = (datetime.now() - session.start_time).total_seconds()
        sessions.append({
            'student_name': student.name,
            'resource_title': resource.title,
            'started_time': session.start_time.strftime('%H:%M'),
            'duration': duration,
            'progress': 0  # Placeholder for progress calculation
        })
    
    return jsonify({'sessions': sessions})

@app.route('/api/teacher/active_students')
@login_required
@teacher_required
def get_active_students():
    """Get currently active students"""
    students = Student.query.filter_by(teacher_id=current_user.id).all()
    student_ids = [s.id for s in students]
    
    # Get currently active students
    active_students = db.session.query(
        Student, Resource
    ).join(
        StudySession, Student.id == StudySession.student_id
    ).join(
        Resource, StudySession.resource_id == Resource.id
    ).filter(
        StudySession.student_id.in_(student_ids),
        StudySession.completed == False,
        StudySession.start_time >= datetime.now() - timedelta(minutes=30)
    ).distinct().all()
    
    students_list = []
    for student, resource in active_students:
        students_list.append({
            'name': student.name,
            'current_resource': resource.title
        })
    
    return jsonify({'students': students_list})

@app.route('/api/teacher/recent_activities')
@login_required
@teacher_required
def get_recent_activities():
    """Get recent student activities"""
    students = Student.query.filter_by(teacher_id=current_user.id).all()
    student_ids = [s.id for s in students]
    
    # Get recent student activities
    recent_activities = db.session.query(
        StudentActivity, Student, Resource
    ).join(
        Student, StudentActivity.student_id == Student.id
    ).join(
        Resource, StudentActivity.resource_id == Resource.id
    ).filter(
        StudentActivity.student_id.in_(student_ids),
        StudentActivity.timestamp >= datetime.now() - timedelta(hours=2)
    ).order_by(
        StudentActivity.timestamp.desc()
    ).limit(20).all()
    
    activities = []
    for activity, student, resource in recent_activities:
        activities.append({
            'student_name': student.name,
            'activity_type': activity.activity_type,
            'resource_title': resource.title,
            'timestamp': activity.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        })
    
    return jsonify({'activities': activities})

@app.route('/api/teacher/student_comprehensive_data/<int:student_id>')
@login_required
@teacher_required
def get_student_comprehensive_data(student_id):
    """Get comprehensive tracking data for a specific student"""
    # Verify teacher has access to this student
    student = Student.query.filter_by(id=student_id, teacher_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get ALL tracking data for this student
    all_activities = StudentActivity.query.filter_by(student_id=student_id).order_by(StudentActivity.timestamp.desc()).all()
    all_engagements = ResourceEngagement.query.filter_by(student_id=student_id).order_by(ResourceEngagement.last_updated.desc()).all()
    all_sessions = StudySession.query.filter_by(student_id=student_id).order_by(StudySession.start_time.desc()).all()
    all_predictions = StudentSuccessPrediction.query.filter_by(student_id=student_id).order_by(StudentSuccessPrediction.created_at.desc()).all()
    
    # Calculate comprehensive statistics
    stats = {
        'total_activities': len(all_activities),
        'total_engagements': len(all_engagements),
        'total_sessions': len(all_sessions),
        'completed_sessions': len([s for s in all_sessions if s.completed]),
        'avg_quiz_score': sum([s.quiz_score or 0 for s in all_sessions if s.quiz_score]) / len([s for s in all_sessions if s.quiz_score]) if any(s.quiz_score for s in all_sessions) else 0,
        'avg_engagement_score': sum([e.engagement_score or 0 for e in all_engagements]) / len(all_engagements) if all_engagements else 0,
        'total_time_spent': sum([e.total_time_spent or 0 for e in all_engagements]),
        'total_clicks': sum([e.clicks or 0 for e in all_engagements]),
        'total_cursor_movements': sum([e.cursor_movements or 0 for e in all_engagements]),
        'activity_types_summary': {}
    }
    
    # Calculate activity type summary
    for activity in all_activities:
        activity_type = activity.activity_type
        if activity_type not in stats['activity_types_summary']:
            stats['activity_types_summary'][activity_type] = 0
        stats['activity_types_summary'][activity_type] += 1
    
    # Group activities by resource for detailed analysis
    activities_by_resource = {}
    for activity in all_activities:
        resource_id = activity.resource_id
        if resource_id not in activities_by_resource:
            activities_by_resource[resource_id] = []
        activities_by_resource[resource_id].append(activity)
    
    return jsonify({
        'student': {
            'id': student.id,
            'name': student.name,
            'student_id': student.student_id,
            'grade': student.grade
        },
        'statistics': stats,
        'activities_summary': len(all_activities),
        'engagements_summary': len(all_engagements),
        'sessions_summary': len(all_sessions),
        'predictions_summary': len(all_predictions),
        'activities_by_resource': len(activities_by_resource),
        'sample_recent_activities': [{
            'activity_type': a.activity_type,
            'timestamp': a.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'resource_id': a.resource_id,
            'session_id': a.session_id
        } for a in all_activities[:20]],
        'sample_recent_engagements': [{
            'resource_id': e.resource_id,
            'session_id': e.session_id,
            'total_time_spent': e.total_time_spent,
            'engagement_score': e.engagement_score,
            'last_updated': e.last_updated.strftime('%Y-%m-%d %H:%M:%S') if e.last_updated else None
        } for e in all_engagements[:10]]
    })

@app.route('/student/quiz_list')
@login_required
def student_quiz_list():
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get all potential quizzes (standalone quiz resources OR resources with attached questions)
    available_quizzes_raw = db.session.query(Resource, QuizMetadata, db.func.count(Question.id).label('question_count')).outerjoin(
        QuizMetadata, Resource.id == QuizMetadata.resource_id
    ).outerjoin(
        Question, Resource.id == Question.resource_id
    ).filter(
        Resource.created_by == student.teacher_id,
        Resource.grade == student.grade
    ).group_by(Resource.id).order_by(Resource.created_at.desc()).all()
    
    # Filter out any None values and ensure we have valid quiz data
    available_quizzes = []
    for quiz_data in available_quizzes_raw:
        resource, metadata, question_count = quiz_data
        if resource is not None and (resource.resource_type == 'quiz' or (question_count or 0) > 0):  # Ensure resource is quiz or has questions
            # Check if student has completed this quiz
            completed_session = StudySession.query.filter_by(
                student_id=student.id,
                resource_id=resource.id,
                completed=True
            ).first()
            
            # Check if teacher has allowed reassessment
            reassessment = QuizReassessment.query.filter_by(
                student_id=student.id,
                resource_id=resource.id,
                is_used=False
            ).first()
            
            available_quizzes.append({
                'resource': resource,
                'metadata': metadata,
                'question_count': question_count or 0,
                'completed': bool(completed_session),
                'has_reassessment': bool(reassessment),
                'completed_session': completed_session
            })
    
    # Get existing assignments for this student to show access key status
    existing_assignments = db.session.query(ResourceAssignment).filter(
        ResourceAssignment.student_id == student.id
    ).all()
    
    # Create a mapping of resource_id to assignment for quick lookup
    assignment_map = {assignment.resource_id: assignment for assignment in existing_assignments}
    
    # Create assignments list with all available quizzes, showing assignment status
    assignments = []
    for quiz_data in available_quizzes:
        resource = quiz_data['resource']
        assignment = assignment_map.get(resource.id)
        if assignment:
            # Student already has an assignment
            assignments.append((resource, assignment))
        else:
            # Create a virtual assignment object for display purposes
            virtual_assignment = type('VirtualAssignment', (), {
                'access_key': None,
                'max_students': 1,
                'assigned_by': student.teacher_id,
                'assigned_at': resource.created_at
            })()
            assignments.append((resource, virtual_assignment))
    
    return render_template('student_quiz_list.html', available_quizzes=available_quizzes, assignments=assignments)

@app.route('/student/view_resource/<int:resource_id>', methods=['GET', 'POST'])
@login_required
def view_resource(resource_id):
    if current_user.role != 'student':
        abort(403)
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    resource = Resource.query.get_or_404(resource_id)
    
    # Determine access via direct assignment or class match (teacher + grade)
    assignment = ResourceAssignment.query.filter_by(
        resource_id=resource_id, 
        student_id=student.id
    ).first()
    class_match = (resource.created_by == student.teacher_id and resource.grade == student.grade)
    # If neither assignment nor class match, require an access key flow (no auto-access)
    require_key = (not class_match and not assignment)
    
    # Check if resource has time limit and if it has expired
    time_limit = getattr(resource, 'access_time_limit', 0) or 0
    if time_limit > 0:
        # For quiz resources, check if marks have been published
        # If marks are published, allow access regardless of time limit
        if resource.resource_type == 'quiz':
            metadata = QuizMetadata.query.filter_by(resource_id=resource_id).first()
            if metadata and metadata.marks_published:
                # Marks have been published, allow access
                pass
            else:
                # Check if there's an existing session and if time has expired
                existing_session = StudySession.query.filter_by(
                    student_id=student.id,
                    resource_id=resource.id
                ).order_by(StudySession.start_time.desc()).first()
                
                if existing_session and existing_session.start_time:
                    from datetime import timedelta
                    expires_at = existing_session.start_time + timedelta(minutes=time_limit)
                    if datetime.now() >= expires_at:
                        # Time has expired; keep resource visible and inform student instead of expiring page
                        flash('Viewing time limit has passed. You can continue to access this resource, but activity tracking may be limited.', 'warning')
        else:
            # For non-quiz resources, check time limit as before
            existing_session = StudySession.query.filter_by(
                student_id=student.id,
                resource_id=resource.id
            ).order_by(StudySession.start_time.desc()).first()
            
            if existing_session and existing_session.start_time:
                from datetime import timedelta
                expires_at = existing_session.start_time + timedelta(minutes=time_limit)
                if datetime.now() >= expires_at:
                    # Time has expired; keep resource visible and inform student instead of expiring page
                    flash('Viewing time limit has passed. You can continue to access this resource, but activity tracking may be limited.', 'warning')
    
    # Create or get existing study session for tracking (do this early for all paths)
    existing_session = StudySession.query.filter_by(
        student_id=student.id,
        resource_id=resource.id,
        completed=False
    ).order_by(StudySession.start_time.desc()).first()
    
    if not existing_session:
        session = StudySession(
            student_id=student.id,
            resource_id=resource.id,
            start_time=datetime.now(),
            completed=False
        )
        db.session.add(session)
        db.session.commit()
    else:
        session = existing_session
    
    # Normalize file path for URLs
    if getattr(resource, 'file_path', None):
        resource.file_path = resource.file_path.replace('\\', '/')
    
    # Prevent direct file download for notes; serve inline through a controlled route
    # Links will be proxied through a tracker route
    
    # If not assigned yet, check if access key is required
    if not assignment:
        # Check if there's an active access key for this resource
        resource_access = ResourceAccess.query.filter_by(resource_id=resource.id, is_active=True).first()
        
        # If no access key exists
        if not resource_access:
            if require_key:
                # No class match and no prior assignment: do not auto-assign across classes/teachers
                abort(403)
            # Auto-grant when this is a class match (resource intended for this student's class)
            assignment = ResourceAssignment(
                resource_id=resource.id,
                student_id=student.id,
                assigned_by=resource.created_by,
                access_key=None,
                max_students=None
            )
            try:
                db.session.add(assignment)
                db.session.commit()
                # Continue to render the resource view below
            except Exception as e:
                db.session.rollback()
                flash(f'Error granting access: {str(e)}', 'danger')
                return redirect(url_for('student_dashboard'))
        else:
            # Access key exists, require it
            if request.method == 'POST':
                access_key = request.form.get('access_key')
                if not access_key:
                    flash('Please enter an access key to view this resource.', 'danger')
                    return render_template('access_key_required.html', resource=resource, session=session)
                if resource_access.access_key != access_key:
                    flash('Invalid access key. Please try again.', 'danger')
                    return render_template('access_key_required.html', resource=resource, session=session)
                # Check usage limit
                if resource_access.current_usage >= resource_access.max_students:
                    flash('This access key has reached its maximum usage limit.', 'danger')
                    return render_template('access_key_required.html', resource=resource, session=session)
                # Create assignment for this student
                assignment = ResourceAssignment(
                    resource_id=resource.id,
                    student_id=student.id,
                    assigned_by=resource_access.created_by,
                    access_key=access_key,
                    max_students=resource_access.max_students
                )
                resource_access.current_usage += 1
                try:
                    db.session.add(assignment)
                    db.session.commit()
                    # Continue to render the resource view below
                except Exception as e:
                    db.session.rollback()
                    flash(f'Error granting access: {str(e)}', 'danger')
                    return render_template('access_key_required.html', resource=resource, session=session)
            else:
                # GET: prompt for key if access key is required
                return render_template('access_key_required.html', resource=resource, session=session)
    
    # Already assigned: enforce access key if one exists on assignment
    # Do NOT auto-generate questions here. Teachers should create quizzes manually.
    questions = Question.query.filter_by(resource_id=resource_id).all()
    
    # Check if marks have been published for quiz resources
    marks_published = False
    if resource.resource_type == 'quiz':
        metadata = QuizMetadata.query.filter_by(resource_id=resource_id).first()
        if metadata and metadata.marks_published:
            marks_published = True
    
    # Check if access key is required and provided
    if request.method == 'POST':
        access_key = request.form.get('access_key')
        if not access_key:
            flash('Please enter an access key to view this resource.', 'danger')
            return render_template('access_key_required.html', resource=resource, session=session)
        if assignment.access_key != access_key:
            flash('Invalid access key. Please try again.', 'danger')
            return render_template('access_key_required.html', resource=resource, session=session)
        return render_template('view_resource.html', resource=resource, session=session, time_limit=time_limit, marks_published=marks_published)
    
    if assignment.access_key:
        return render_template('access_key_required.html', resource=resource, session=session)
    else:
        return render_template('view_resource.html', resource=resource, session=session, time_limit=time_limit, marks_published=marks_published)


@app.route('/api/debug/sessions')
@login_required
def debug_sessions():
    """Debug endpoint to check recent sessions"""
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    # Get recent sessions for this student
    recent_sessions = StudySession.query.filter_by(
        student_id=student.id
    ).order_by(StudySession.start_time.desc()).limit(10).all()
    
    sessions_data = []
    for session in recent_sessions:
        resource = Resource.query.get(session.resource_id)
        sessions_data.append({
            'session_id': session.id,
            'resource_id': session.resource_id,
            'resource_title': resource.title if resource else 'Unknown',
            'start_time': session.start_time.isoformat(),
            'completed': session.completed,
            'duration': session.duration
        })
    
    return jsonify({
        'student_id': student.id,
        'sessions': sessions_data
    })

@app.route('/api/debug/all_sessions')
@login_required
@teacher_required
def debug_all_sessions():
    """Debug endpoint to check all sessions for teachers"""
    # Get all sessions for debugging
    sessions = StudySession.query.order_by(StudySession.start_time.desc()).limit(20).all()
    
    sessions_data = []
    for session in sessions:
        student = Student.query.get(session.student_id)
        resource = Resource.query.get(session.resource_id)
        sessions_data.append({
            'id': session.id,
            'student_id': session.student_id,
            'student_name': student.name if student else 'Unknown',
            'resource_id': session.resource_id,
            'resource_title': resource.title if resource else 'Unknown',
            'start_time': session.start_time.isoformat() if session.start_time else None,
            'end_time': session.end_time.isoformat() if session.end_time else None,
            'duration': session.duration,
            'quiz_score': session.quiz_score,
            'completed': session.completed
        })
    
    return jsonify({
        'total_sessions': len(sessions_data),
        'sessions': sessions_data
    })

@app.route('/api/debug/fix_engagement_data')
@login_required
@teacher_required
def fix_engagement_data():
    """Fix engagement data that exceeds session duration"""
    fixed_count = 0
    
    # Get all engagement records
    engagements = ResourceEngagement.query.all()
    
    for engagement in engagements:
        # Get the corresponding session
        session = StudySession.query.get(engagement.session_id)
        if session and session.start_time:
            # Calculate actual session duration
            if session.end_time:
                actual_duration = int((session.end_time - session.start_time).total_seconds())
            else:
                actual_duration = int((datetime.now() - session.start_time).total_seconds())
            
            # Fix metrics that exceed session duration
            if engagement.total_time_spent and engagement.total_time_spent > actual_duration:
                engagement.total_time_spent = actual_duration
                fixed_count += 1
            
            if engagement.focus_time and engagement.focus_time > actual_duration:
                engagement.focus_time = actual_duration
                fixed_count += 1
            
            if engagement.idle_time and engagement.idle_time > actual_duration:
                engagement.idle_time = actual_duration
                fixed_count += 1
    
    db.session.commit()
    
    return jsonify({
        'message': f'Fixed {fixed_count} engagement records',
        'fixed_count': fixed_count
    })

@app.route('/student/start_study/<int:resource_id>', methods=['POST'])
@login_required
def start_study(resource_id):
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    try:
        resource = Resource.query.get_or_404(resource_id)
        if resource.grade != student.grade:
            abort(403)
        
        # Check if there's already an active session for this resource
        existing_session = StudySession.query.filter_by(
            student_id=student.id,
            resource_id=resource_id,
            completed=False
        ).first()
        
        if existing_session:
            return jsonify({'success': True, 'session_id': existing_session.id, 'message': 'Active session already exists'})
        
        session = StudySession(
            student_id=student.id,
            resource_id=resource_id,
            start_time=datetime.now()
        )
        db.session.add(session)
        db.session.commit()
        
        return jsonify({'success': True, 'session_id': session.id})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/check_quiz_exists/<int:resource_id>')
@login_required
def check_quiz_exists(resource_id):
    """Check if a quiz exists for a given resource"""
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    resource = Resource.query.get_or_404(resource_id)
    
    # Check if student can access this resource
    if resource.created_by != student.teacher_id or resource.grade != student.grade:
        abort(403)
    
    # Check if questions exist for this resource
    questions_count = Question.query.filter_by(resource_id=resource_id).count()
    
    return jsonify({
        'has_quiz': questions_count > 0,
        'questions_count': questions_count
    })

@app.route('/student/end_study/<int:session_id>', methods=['POST'])
@login_required
def end_study(session_id):
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    try:
        session = StudySession.query.get_or_404(session_id)
        if session.student_id != student.id:
            abort(403)
        
        # Check if session is already completed
        if session.completed:
            return jsonify({'success': True, 'message': 'Session already completed'})
        
        session.end_time = datetime.now()
        session.duration = int((session.end_time - session.start_time).total_seconds())
        
        # Only mark as completed if there are questions for this resource (i.e., it's a quiz)
        total_questions = Question.query.filter_by(resource_id=session.resource_id).count()
        if total_questions > 0:
            session.completed = True
            # Generate AI recommendation based on performance
            try:
                session.ai_recommendation = generate_ai_recommendation(session)
            except Exception as e:
                print(f"Error generating AI recommendation: {str(e)}")
                session.ai_recommendation = "Recommendation not available at this time."
        else:
            # For non-quiz resources, just mark as study completed but not quiz completed
            session.completed = False
        
        db.session.commit()
        
        return jsonify({'success': True, 'duration': session.duration})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/get_session_start_time/<int:session_id>')
@login_required
def get_session_start_time(session_id):
    """Get the start time of a study session"""
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    try:
        session = StudySession.query.get_or_404(session_id)
        if session.student_id != student.id:
            abort(403)
        
        return jsonify({
            'success': True,
            'start_time': session.start_time.isoformat() if session.start_time else None
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/resource_expired/<int:resource_id>')
@login_required
def resource_expired(resource_id):
    """Display expired resource page"""
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    resource = Resource.query.get_or_404(resource_id)
    
    # Check if student can access this resource
    if resource.created_by != student.teacher_id or resource.grade != student.grade:
        abort(403)
    
    # If a quiz exists, offer a direct button to start it
    questions_count = Question.query.filter_by(resource_id=resource_id).count()
    return render_template('resource_expired.html', resource=resource, has_quiz=(questions_count > 0))

@app.route('/api/save_notes/<int:resource_id>', methods=['POST'])
@login_required
def save_student_notes(resource_id):
    """Save or update student notes for a resource"""
    if current_user.role != 'student':
        return jsonify({'success': False, 'error': 'Only students can save notes'}), 403
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return jsonify({'success': False, 'error': 'Student profile not found'}), 404
    
    # Verify resource exists and is accessible to student
    resource = Resource.query.get_or_404(resource_id)
    
    # Check if student has access to this resource
    assignment = ResourceAssignment.query.filter_by(
        resource_id=resource_id,
        student_id=student.id
    ).first()
    
    if not assignment and not (resource.created_by == student.teacher_id and resource.grade == student.grade):
        return jsonify({'success': False, 'error': 'You do not have access to this resource'}), 403
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
            
        notes_content = data.get('notes', '').strip()
        
        # Allow empty notes to clear existing ones
        if notes_content == '':
            notes_content = ''  # Explicitly allow empty string to clear notes
        
        # Get or create the current study session
        session = StudySession.query.filter_by(
            student_id=student.id,
            resource_id=resource_id,
            completed=False
        ).order_by(StudySession.start_time.desc()).first()
        
        if not session:
            session = StudySession(
                student_id=student.id,
                resource_id=resource_id,
                start_time=datetime.now(),
                completed=False
            )
            db.session.add(session)
            db.session.flush()  # To get the session ID
        
        # Check if notes already exist for this student and resource
        existing_notes = StudentNotes.query.filter_by(
            student_id=student.id,
            resource_id=resource_id
        ).first()
        
        # Calculate engagement metrics for notes
        word_count = len(notes_content.split()) if notes_content else 0
        character_count = len(notes_content) if notes_content else 0
        # Simple engagement score: normalize word and character counts, clamp to 0-100
        def _clamp(v, lo=0.0, hi=100.0):
            return max(lo, min(hi, v))
        # Heuristic: 200 words ~ 100 points; characters adds small weight
        note_engagement_score = _clamp((word_count / 200.0) * 90.0 + (character_count / 2000.0) * 10.0)

        if existing_notes:
            # Update existing notes
            existing_notes.notes_content = notes_content
            existing_notes.updated_at = datetime.now()
            existing_notes.word_count = word_count
            existing_notes.character_count = character_count
            existing_notes.engagement_score = note_engagement_score
            notes_id = existing_notes.id
        else:
            # Create new notes
            new_notes = StudentNotes(
                student_id=student.id,
                resource_id=resource_id,
                notes_content=notes_content,
                word_count=word_count,
                character_count=character_count,
                engagement_score=note_engagement_score
            )
            db.session.add(new_notes)
            db.session.flush()  # To get the new notes ID
            notes_id = new_notes.id
        
        # Track the notes save activity
        try:
            activity = StudentActivity(
                student_id=student.id,
                resource_id=resource_id,
                study_session_id=session.id,
                activity_type='notes_save',
                data={
                    'notes_id': notes_id,
                    'notes_length': len(notes_content),
                    'timestamp': datetime.now().isoformat(),
                    'char_count': len(notes_content),
                    'word_count': len(notes_content.split()) if notes_content else 0
                }
            )
            db.session.add(activity)
        except Exception as e:
            print(f"Error creating activity log: {str(e)}")
            # Don't fail the whole request if activity logging fails
        
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Notes saved successfully',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'notes_id': notes_id,
            'word_count': word_count,
            'character_count': character_count,
            'engagement_score': round(note_engagement_score, 1)
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error saving notes: {str(e)}")
        return jsonify({'success': False, 'error': f'Failed to save notes: {str(e)}'}), 500
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/get_notes/<int:resource_id>')
@login_required
def get_student_notes(resource_id):
    """Get student notes for a resource"""
    if current_user.role != 'student':
        return jsonify({'success': False, 'error': 'Only students can view notes'}), 403
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return jsonify({'success': False, 'error': 'Student profile not found'}), 404
    
    # Verify resource exists and is accessible to student
    resource = Resource.query.get_or_404(resource_id)
    
    # Check if student has access to this resource
    assignment = ResourceAssignment.query.filter_by(
        resource_id=resource_id,
        student_id=student.id
    ).first()
    
    if not assignment and not (resource.created_by == student.teacher_id and resource.grade == student.grade):
        return jsonify({'success': False, 'error': 'You do not have access to this resource'}), 403
    
    try:
        # Get the notes for this student and resource
        notes = StudentNotes.query.filter_by(
            student_id=student.id,
            resource_id=resource_id
        ).first()
        
        # Get the last activity timestamp for these notes
        last_activity = None
        if notes:
            last_activity = StudentActivity.query.filter_by(
                student_id=student.id,
                resource_id=resource_id,
                activity_type='notes_save'
            ).order_by(StudentActivity.timestamp.desc()).first()
        
        # Get the current study session
        session = StudySession.query.filter_by(
            student_id=student.id,
            resource_id=resource_id,
            completed=False
        ).order_by(StudySession.start_time.desc()).first()
        
        if notes:
            response_data = {
                'success': True,
                'notes': notes.notes_content,
                'last_updated': notes.updated_at.isoformat() if notes.updated_at else notes.created_at.isoformat(),
                'has_notes': True,
                'char_count': len(notes.notes_content) if notes.notes_content else 0,
                'word_count': len(notes.notes_content.split()) if notes.notes_content else 0,
                'session_active': session is not None,
                'session_id': session.id if session else None
            }
            
            # Add activity data if available
            if last_activity and hasattr(last_activity, 'data'):
                response_data.update({
                    'last_activity': last_activity.timestamp.isoformat(),
                    'activity_data': last_activity.data
                })
                
            return jsonify(response_data)
        else:
            # Return empty notes with session info
            return jsonify({
                'success': True,
                'notes': '',
                'has_notes': False,
                'session_active': session is not None,
                'session_id': session.id if session else None,
                'char_count': 0,
                'word_count': 0
            })
    except Exception as e:
        print(f"Error retrieving notes: {str(e)}")
        return jsonify({
            'success': False, 
            'error': 'Failed to retrieve notes',
            'details': str(e)
        }), 500

@app.route('/teacher/view_student_notes/<int:resource_id>')
@login_required
@teacher_required
def view_student_notes(resource_id):
    """Teacher view of all student notes for a resource"""
    resource = Resource.query.get_or_404(resource_id)
    
    # Ensure teacher owns this resource
    if resource.created_by != current_user.id:
        abort(403, "You don't have permission to view notes for this resource.")
    
    # Get all students assigned to this teacher
    teacher_students = Student.query.filter_by(teacher_id=current_user.id).all()
    student_ids = [s.id for s in teacher_students]
    
    # Optional filters
    filter_student_id = request.args.get('student_id', type=int)
    filter_name = request.args.get('name', '').strip()
    filter_grade = request.args.get('grade', type=float)
    filter_has_notes = request.args.get('has_notes', type=str) == 'true'
    filter_graded = request.args.get('graded', type=str)  # 'graded', 'ungraded', or None
    
    # Base query for student notes
    base_query = db.session.query(
        StudentNotes,
        Student,
        User
    ).join(
        Student, StudentNotes.student_id == Student.id
    ).join(
        User, Student.user_id == User.id
    ).filter(
        StudentNotes.resource_id == resource_id,
        Student.teacher_id == current_user.id
    )
    
    # Apply filters
    if filter_student_id:
        base_query = base_query.filter(StudentNotes.student_id == filter_student_id)
    
    if filter_name:
        like = f"%{filter_name}%"
        base_query = base_query.filter(
            db.or_(
                User.first_name.ilike(like),
                User.last_name.ilike(like),
                User.username.ilike(like),
                Student.name.ilike(like)
            )
        )
    
    if filter_grade is not None:
        base_query = base_query.filter(StudentNotes.teacher_grade >= filter_grade)
    
    if filter_has_notes:
        base_query = base_query.filter(StudentNotes.notes_content != '')
    
    if filter_graded == 'graded':
        base_query = base_query.filter(StudentNotes.teacher_grade.isnot(None))
    elif filter_graded == 'ungraded':
        base_query = base_query.filter(StudentNotes.teacher_grade.is_(None))
    
    # Get all matching notes
    notes_data = base_query.order_by(
        db.desc(StudentNotes.updated_at)
    ).all()
    
    # Get all students (including those without notes) if no student_id filter is applied
    if not filter_student_id:
        students_with_notes = {student.id for _, student, _ in notes_data}
        students_without_notes = []
        
        # Find students who don't have notes yet
        for student in teacher_students:
            if student.id not in students_with_notes:
                user = User.query.get(student.user_id)
                students_without_notes.append((None, student, user))
        
        # Combine both lists
        all_students_data = list(notes_data) + students_without_notes
    else:
        all_students_data = notes_data
    
    # Get activity data for each student
    activity_data = {}
    student_notes_map = {}
    
    for notes, student, user in all_students_data:
        student_id = student.id
        student_notes_map[student_id] = notes
        
        # Get recent activities for this student and resource
        activities = StudentActivity.query.filter(
            StudentActivity.student_id == student_id,
            StudentActivity.resource_id == resource_id,
            StudentActivity.activity_type.in_(['notes_save', 'resource_view', 'quiz_attempt'])
        ).order_by(
            StudentActivity.timestamp.desc()
        ).limit(15).all()
        
        activity_data[student_id] = activities
    
    # Calculate statistics
    total_students = len(all_students_data)
    students_with_notes = sum(1 for item in all_students_data if item[0] and item[0].notes_content.strip())
    
    # Get grading stats
    graded_notes = [item[0] for item in all_students_data if item[0] and item[0].teacher_grade is not None]
    graded_count = len(graded_notes)
    pending_count = total_students - graded_count
    
    if graded_count > 0:
        avg_grade = sum(n.teacher_grade for n in graded_notes) / graded_count
        min_grade = min(n.teacher_grade for n in graded_notes)
        max_grade = max(n.teacher_grade for n in graded_notes)
    else:
        avg_grade = min_grade = max_grade = None
    
    # Get word count statistics
    word_counts = [
        len(item[0].notes_content.split()) 
        for item in all_students_data 
        if item[0] and item[0].notes_content
    ]
    
    word_stats = {
        'total': sum(word_counts) if word_counts else 0,
        'avg': sum(word_counts) / len(word_counts) if word_counts else 0,
        'min': min(word_counts) if word_counts else 0,
        'max': max(word_counts) if word_counts else 0,
        'count': len(word_counts)
    }
    
    # Get activity summary
    activity_summary = db.session.query(
        StudentActivity.activity_type,
        db.func.count(StudentActivity.id).label('count')
    ).filter(
        StudentActivity.resource_id == resource_id,
        StudentActivity.student_id.in_(student_ids)
    ).group_by(
        StudentActivity.activity_type
    ).all()
    
    # Get unique student count for this resource
    unique_students = db.session.query(
        StudentActivity.student_id
    ).filter(
        StudentActivity.resource_id == resource_id,
        StudentActivity.student_id.in_(student_ids)
    ).distinct().count()
    
    # Get notes creation timeline
    notes_timeline = db.session.query(
        db.func.date(StudentNotes.created_at).label('date'),
        db.func.count(StudentNotes.id).label('count')
    ).filter(
        StudentNotes.resource_id == resource_id,
        StudentNotes.student_id.in_(student_ids)
    ).group_by(
        db.func.date(StudentNotes.created_at)
    ).order_by(
        db.func.date(StudentNotes.created_at)
    ).all()
    
    # Prepare data for the template
    context = {
        'resource': resource,
        'notes_data': all_students_data,
        'activity_data': activity_data,
        'student_notes_map': student_notes_map,
        'getActivityIcon': getActivityIcon,
        'getActivityTitle': getActivityTitle,
        'getActivityDescription': getActivityDescription,
        'total_students': total_students,
        'students_with_notes': students_with_notes,
        'graded_count': graded_count,
        'pending_count': pending_count,
        'avg_grade': avg_grade,
        'min_grade': min_grade,
        'max_grade': max_grade,
        'word_stats': word_stats,
        'activity_summary': dict(activity_summary),
        'unique_students': unique_students,
        'notes_timeline': [{'date': str(tl[0]), 'count': tl[1]} for tl in notes_timeline],
        'filter_student_id': filter_student_id,
        'filter_name': filter_name,
        'filter_grade': filter_grade,
        'filter_has_notes': filter_has_notes,
        'filter_graded': filter_graded,
        'teacher_students': teacher_students
    }
    
    return render_template('teacher_view_notes.html', **context)


@app.route('/teacher/export_student_notes_csv/<int:resource_id>')
@login_required
@teacher_required
def export_student_notes_csv(resource_id):
    """Export student notes for a resource to CSV with optional filters"""
    resource = Resource.query.get_or_404(resource_id)
    if resource.created_by != current_user.id:
        abort(403)

    filter_student_id = request.args.get('student_id', type=int)
    filter_name = request.args.get('name', type=str)

    base_query = db.session.query(
        StudentNotes,
        Student,
        User
    ).join(
        Student, StudentNotes.student_id == Student.id
    ).join(
        User, Student.user_id == User.id
    ).filter(
        StudentNotes.resource_id == resource_id,
        Student.teacher_id == current_user.id
    )

    if filter_student_id:
        base_query = base_query.filter(StudentNotes.student_id == filter_student_id)
    if filter_name:
        like = f"%{filter_name.strip()}%"
        base_query = base_query.filter(User.username.ilike(like) | Student.name.ilike(like))

    rows = base_query.order_by(StudentNotes.updated_at.desc()).all()

    # Build CSV in-memory
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Student Name', 'Username', 'Student Internal ID', 'Notes', 'Updated At'])
    for notes, student, user in rows:
        writer.writerow([
            getattr(student, 'name', ''),
            getattr(user, 'username', ''),
            getattr(student, 'id', ''),
            (notes.notes_content or '').replace('\n', ' ').strip(),
            notes.updated_at.strftime('%Y-%m-%d %H:%M') if notes.updated_at else ''
        ])

    output.seek(0)
    filename = f"notes_resource_{resource_id}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={"Content-disposition": f"attachment; filename={filename}"}
    )
def getActivityIcon(activity_type):
    """Get appropriate icon for activity type"""
    icon_map = {
        'page_view': 'eye',
        'scroll': 'arrows-alt-v',
        'click': 'mouse-pointer',
        'cursor_move': 'hand-pointer',
        'focus_time': 'clock',
        'idle_time': 'pause',
        'time_spent': 'stopwatch',
        'page_hidden': 'eye-slash',
        'page_visible': 'eye',
        'reading_speed': 'book-reader',
        'comprehension_check': 'question-circle',
        'video_play': 'play',
        'video_pause': 'pause',
        'video_progress': 'forward',
        'video_complete': 'check-circle',
        'video_seek': 'search',
        'reading_started': 'book-open',
        'content_analysis': 'file-alt',
        'session_end': 'sign-out-alt',
        'paste': 'clipboard',
        'notes_save': 'save',
        'notes_auto_save': 'save'
    }
    return icon_map.get(activity_type, 'circle')

def getActivityTitle(activity_type):
    """Get human-readable title for activity type"""
    title_map = {
        'page_view': 'Page Viewed',
        'scroll': 'Scrolled',
        'click': 'Clicked',
        'cursor_move': 'Mouse Movement',
        'focus_time': 'Focused',
        'idle_time': 'Idle Time',
        'time_spent': 'Time Spent',
        'page_hidden': 'Page Hidden',
        'page_visible': 'Page Visible',
        'reading_speed': 'Reading Speed',
        'comprehension_check': 'Comprehension Check',
        'video_play': 'Video Played',
        'video_pause': 'Video Paused',
        'video_progress': 'Video Progress',
        'video_complete': 'Video Completed',
        'video_seek': 'Video Seek',
        'reading_started': 'Reading Started',
        'content_analysis': 'Content Analysis',
        'session_end': 'Session Ended',
        'paste': 'Content Pasted',
        'notes_save': 'Notes Saved',
        'notes_auto_save': 'Notes Auto-saved'
    }
    return title_map.get(activity_type, activity_type.replace('_', ' ').title())

def getActivityDescription(activity):
    """Get detailed description for activity"""
    data = activity.data or {}
    activity_type = activity.activity_type
    
    if activity_type == 'scroll':
        return f"Scrolled to {data.get('scroll_percentage', 0):.1f}% of page"
    elif activity_type == 'click':
        return f"Clicked on {data.get('element', 'element')}"
    elif activity_type == 'focus_time':
        return f"Focused for {data.get('duration', 0)} seconds"
    elif activity_type == 'idle_time':
        return f"Idle for {data.get('duration', 0)} seconds"
    elif activity_type == 'time_spent':
        return f"Spent {data.get('duration', 0)} seconds on page"
    elif activity_type == 'reading_speed':
        return f"Reading at {data.get('wpm', 0)} words per minute"
    elif activity_type == 'comprehension_check':
        return f"Score: {data.get('score', 0):.1f}%"
    elif activity_type == 'video_play':
        return f"Started video at {data.get('currentTime', 0):.1f}s"
    elif activity_type == 'video_pause':
        return f"Paused video at {data.get('currentTime', 0):.1f}s"
    elif activity_type == 'video_progress':
        return f"Video progress: {data.get('progress', 0):.1f}%"
    elif activity_type == 'video_complete':
        return f"Completed {data.get('duration', 0):.1f}s video"
    elif activity_type == 'video_seek':
        return f"Sought to {data.get('currentTime', 0):.1f}s"
    elif activity_type == 'content_analysis':
        return f"Analyzed {data.get('word_count', 0)} words"
    elif activity_type == 'session_end':
        return f"Session lasted {data.get('total_time_spent', 0)} seconds"
    elif activity_type == 'notes_save':
        return "Manually saved notes"
    elif activity_type == 'notes_auto_save':
        return "Auto-saved notes"
    else:
        return "Activity recorded"

@app.route('/student/quiz/<int:resource_id>')
@login_required
def student_quiz(resource_id):
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    resource = Resource.query.get_or_404(resource_id)
    if resource.grade != student.grade:
        abort(403)
    
    # Check if student has access to this quiz (must be from their teacher and correct grade)
    if resource.created_by != student.teacher_id:
        abort(403)
    
    # Check if quiz is deleted and show appropriate message
    if resource.is_deleted:
        flash('This quiz has been deleted by your teacher, but you can still view your previous attempts and results.', 'info')
    
    # Check if student has already completed this quiz
    completed_session = StudySession.query.filter_by(
        student_id=student.id,
        resource_id=resource_id,
        completed=True
    ).first()
    
    # Check if teacher has allowed reassessment
    reassessment = QuizReassessment.query.filter_by(
        student_id=student.id,
        resource_id=resource_id,
        is_used=False
    ).first()
    
    if completed_session and not reassessment:
        # Show completed quiz page with results - NO NEW SESSION CREATION
        questions = Question.query.filter_by(resource_id=resource_id).all()
        
        # Check if marks are published
        metadata = QuizMetadata.query.filter_by(resource_id=resource_id).first()
        marks_published = metadata and metadata.marks_published if metadata else False
        
        # Get detailed marks if published
        detailed_marks = None
        if marks_published:
            student_answers = {}
            for question in questions:
                answer = StudentAnswer.query.filter_by(
                    student_id=student.id,
                    question_id=question.id
                ).first()
                if answer:
                    student_answers[question.id] = answer
            
            # Calculate detailed marks
            total_marks = sum(q.marks for q in questions)
            earned_marks = 0
            for question in questions:
                if question.id in student_answers:
                    answer = student_answers[question.id]
                    if question.question_type == 'mcq':
                        if answer.is_correct:
                            earned_marks += question.marks
                    else:
                        if answer.marks_awarded is not None:
                            earned_marks += answer.marks_awarded
            
            detailed_marks = {
                'total_marks': total_marks,
                'earned_marks': earned_marks,
                'percentage': (earned_marks / total_marks * 100) if total_marks > 0 else 0
            }
        
        return render_template('quiz_completed.html',
                             resource=resource,
                             completed_session=completed_session,
                             questions=questions,
                             marks_published=marks_published,
                             detailed_marks=detailed_marks)
    
    # Only create new session if quiz is not completed
    # Create or get existing study session for this quiz
    existing_session = StudySession.query.filter_by(
        student_id=student.id,
        resource_id=resource_id,
        completed=False
    ).order_by(StudySession.start_time.desc()).first()
    
    if not existing_session:
        # Create new study session
        session = StudySession(
            student_id=student.id,
            resource_id=resource_id,
            start_time=datetime.now()
        )
        db.session.add(session)
        db.session.commit()
    
    questions = Question.query.filter_by(resource_id=resource_id).all()
    
    # If no questions exist for this resource, redirect to dashboard with message
    if not questions:
        flash('No quiz has been created for this resource yet. Please ask your teacher to create a quiz.', 'info')
        return redirect(url_for('student_dashboard'))
    
    # Get quiz metadata for time limit
    quiz_metadata = QuizMetadata.query.filter_by(resource_id=resource_id).first()
    time_limit_seconds = quiz_metadata.time_limit if quiz_metadata else None
    
    # If this is a reassessment, shuffle the options
    if reassessment:
        import random
        # Use a consistent seed based on student ID and resource ID for consistent shuffling
        seed_value = hash(f"{student.id}_{resource_id}") % (2**32)
        random.seed(seed_value)
        
        for question in questions:
            if question.question_type == 'mcq' and question.options and len(question.options) > 1:
                # Create a copy of options to shuffle
                shuffled_options = question.options.copy()
                
                # Ensure the correct answer gets a different position to prevent cheating
                if question.correct_answer in ['A', 'B', 'C', 'D']:
                    letter_to_index = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
                    original_correct_index = letter_to_index.get(question.correct_answer, 0)
                    original_correct_option = question.options[original_correct_index]
                    
                    # First, remove the correct answer from the list
                    remaining_options = [opt for opt in question.options if opt != original_correct_option]
                    
                    # Shuffle the remaining options
                    random.shuffle(remaining_options)
                    
                    # Insert the correct answer at a random position (but not the same position)
                    insert_position = random.randint(0, len(remaining_options))
                    remaining_options.insert(insert_position, original_correct_option)
                    
                    # Update the question with shuffled options
                    question.shuffled_options = remaining_options
                    
                    # Find the new position of the correct option
                    new_correct_index = remaining_options.index(original_correct_option)
                    index_to_letter = {0: 'A', 1: 'B', 2: 'C', 3: 'D'}
                    question.shuffled_correct = index_to_letter[new_correct_index]
                    
                    # Ensure the correct answer is NOT in the same position
                    if question.shuffled_correct == question.correct_answer:
                        # If it ended up in the same position, swap with a different position
                        swap_position = (new_correct_index + 1) % len(remaining_options)
                        remaining_options[new_correct_index], remaining_options[swap_position] = remaining_options[swap_position], remaining_options[new_correct_index]
                        question.shuffled_correct = index_to_letter[swap_position]
                        question.shuffled_options = remaining_options
                else:
                    # For non-letter answers, just shuffle normally
                    random.shuffle(shuffled_options)
                    question.shuffled_options = shuffled_options
                    question.shuffled_correct = question.correct_answer
            else:
                question.shuffled_options = question.options
                question.shuffled_correct = question.correct_answer
        
        # Reset random seed to avoid affecting other parts of the application
        random.seed()
    
    return render_template('student_quiz.html', 
                         resource=resource, 
                         questions=questions, 
                         is_reassessment=bool(reassessment),
                         time_limit_seconds=time_limit_seconds,
                         quiz_metadata=quiz_metadata)

@app.route('/student/submit_answer', methods=['POST'])
@login_required
def submit_answer():
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    question_id = request.form.get('question_id')
    answer = request.form.get('answer')
    resource_id = request.form.get('resource_id')
    
    # Debug: Log answer submission
    print(f"Submit answer - Student: {student.id}, Question: {question_id}, Answer: {answer}, Resource: {resource_id}")

    # If time has expired, finalize immediately even if no answer was selected
    try:
        if resource_id:
            rid_int = int(resource_id)
            quiz_metadata = QuizMetadata.query.filter_by(resource_id=rid_int).first()
            if quiz_metadata and (quiz_metadata.time_limit or 0) > 0:
                session = StudySession.query.filter_by(
                    student_id=student.id,
                    resource_id=rid_int
                ).order_by(StudySession.start_time.desc()).first()
                if session and session.start_time and not session.completed:
                    from datetime import timedelta
                    expires_at = session.start_time + timedelta(seconds=int(quiz_metadata.time_limit))
                    if datetime.now() >= expires_at:
                        _final_session, _ai = _finalize_quiz_session(student, rid_int)
                        total_questions = Question.query.filter_by(resource_id=rid_int).filter(Question.question_type == 'mcq').count()
                        correct_answers = StudentAnswer.query.join(Question).filter(
                            StudentAnswer.student_id == student.id,
                            Question.resource_id == rid_int,
                            Question.question_type == 'mcq',
                            StudentAnswer.is_correct == True
                        ).count()
                        return jsonify({
                            'success': True,
                            'timeout': True,
                            'final_score': _final_session.quiz_score,
                            'correct_answers': correct_answers,
                            'total_questions': total_questions,
                            'ai_recommendation': _ai
                        })
    except Exception:
        pass

    if not all([question_id, answer, resource_id]):
        return jsonify({'success': False, 'error': 'Please answer all questions.'})
    
    try:
        question_id = int(question_id)
        resource_id = int(resource_id)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid question or resource ID'})
    
    question = Question.query.get_or_404(question_id)
    resource = Resource.query.get_or_404(resource_id)

    # Enforce time limit: if exceeded, finalize quiz automatically
    try:
        quiz_metadata = QuizMetadata.query.filter_by(resource_id=resource_id).first()
        if quiz_metadata and (quiz_metadata.time_limit or 0) > 0:
            session = StudySession.query.filter_by(
                student_id=student.id,
                resource_id=resource_id
            ).order_by(StudySession.start_time.desc()).first()
            if session and session.start_time and not session.completed:
                from datetime import timedelta
                expires_at = session.start_time + timedelta(seconds=int(quiz_metadata.time_limit))
                if datetime.now() >= expires_at:
                    # Time is up; finalize and return timeout response
                    _final_session, _ai = _finalize_quiz_session(student, resource_id)
                    total_questions = Question.query.filter_by(resource_id=resource_id).filter(Question.question_type == 'mcq').count()
                    correct_answers = StudentAnswer.query.join(Question).filter(
                        StudentAnswer.student_id == student.id,
                        Question.resource_id == resource_id,
                        Question.question_type == 'mcq',
                        StudentAnswer.is_correct == True
                    ).count()
                    return jsonify({
                        'success': True,
                        'timeout': True,
                        'final_score': _final_session.quiz_score,
                        'correct_answers': correct_answers,
                        'total_questions': total_questions,
                        'ai_recommendation': _ai
                    })
    except Exception:
        # If any error occurs in timeout check, fall through and continue normal processing
        pass
    
    # Check if this question belongs to the specified resource
    if question.resource_id != resource_id:
        return jsonify({'success': False, 'error': 'Invalid question for this resource'})
    
    # Check if student has access to this resource
    # First check for direct assignment
    assignment = ResourceAssignment.query.filter_by(
        resource_id=resource_id,
        student_id=student.id
    ).first()
    
    # If no assignment, check if resource is publicly accessible or if student has any study session
    has_access = False
    if assignment:
        has_access = True
    else:
        # Check if there's any study session for this resource (indicating previous access)
        any_session = StudySession.query.filter_by(
            student_id=student.id,
            resource_id=resource_id
        ).first()
        
        if any_session:
            has_access = True
        else:
            # Check if this is a public resource (no access restrictions)
            resource_accesses = ResourceAccess.query.filter_by(resource_id=resource_id).count()
            if resource_accesses == 0:
                # No access restrictions, allow access
                has_access = True
    
    if not has_access:
        return jsonify({'success': False, 'error': 'Access denied to this resource'})
    
    # Check if this is a reassessment with shuffled options
    reassessment = QuizReassessment.query.filter_by(
        student_id=student.id,
        resource_id=resource_id,
        is_used=False
    ).first()
    
    # Normalize answer for robust matching
    if isinstance(answer, str):
        answer = answer.strip()

    # Auto-grade MCQ, manual-grade essay
    is_correct = None
    shuffled_correct = None
    if question.question_type == 'mcq' and question.correct_answer in ['A', 'B', 'C', 'D']:
        # Check if this is a reassessment with shuffled options
        if reassessment and question.options and len(question.options) > 1:
            import random
            # Use a consistent seed based on student and resource to reproduce shuffle
            seed_value = hash(f"{student.id}_{resource_id}") % (2**32)
            random.seed(seed_value)
            letter_to_index = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
            original_correct_index = letter_to_index.get(question.correct_answer, 0)
            original_correct_option = question.options[original_correct_index]
            remaining_options = [opt for opt in question.options if opt != original_correct_option]
            random.shuffle(remaining_options)
            insert_position = random.randint(0, len(remaining_options))
            remaining_options.insert(insert_position, original_correct_option)
            try:
                selected_index = remaining_options.index(answer)
                letter_map = {0: 'A', 1: 'B', 2: 'C', 3: 'D'}
                selected_letter = letter_map.get(selected_index)
                shuffled_correct = letter_map.get(insert_position)
                is_correct = selected_letter == shuffled_correct
            except (ValueError, IndexError):
                is_correct = False
            random.seed()
        else:
            # Non-reassessment or no shuffle: map selected option text to index
            try:
                # Robust index lookup: ignore extra spaces/case differences
                normalized_options = [(opt or '').strip() for opt in (question.options or [])]
                selected_index = next((i for i, opt in enumerate(normalized_options) if opt.lower() == (answer or '').strip().lower()), None)
                if selected_index is None:
                    raise ValueError('option not found')
                letter_map = {0: 'A', 1: 'B', 2: 'C', 3: 'D'}
                selected_letter = letter_map.get(selected_index)
                is_correct = selected_letter == question.correct_answer
            except (ValueError, IndexError):
                is_correct = False
            shuffled_correct = question.correct_answer
    
    # Check if student already answered this question
    existing_answer = StudentAnswer.query.filter_by(
        student_id=student.id,
        question_id=question_id
    ).first()
    
    if existing_answer:
        # Update existing answer (persist even if empty string)
        existing_answer.answer = answer if answer is not None else ''
        existing_answer.is_correct = is_correct
        existing_answer.submitted_at = datetime.now()
        print(f"Updating existing answer: {existing_answer.answer} for question {question_id}")
        # Compute plagiarism only for essay answers
        if question.question_type == 'essay' and answer and answer.strip():
            try:
                # Find other students' answers to the same question (most recent per student)
                other_answers = StudentAnswer.query.filter(
                    StudentAnswer.question_id == question_id,
                    StudentAnswer.student_id != student.id
                ).all()
                best_score = 0.0
                best_match = None
                for oa in other_answers:
                    s = difflib.SequenceMatcher(None, (answer or '').strip().lower(), (oa.answer or '').strip().lower())
                    score = s.ratio()
                    if score > best_score:
                        best_score = score
                        best_match = oa
                existing_answer.plagiarism_score = round(best_score, 4) if best_score else None
                if best_match and best_score >= 0.85:  # high similarity threshold
                    existing_answer.plagiarism_match_student_id = best_match.student_id
                    existing_answer.plagiarism_match_answer_id = best_match.id
                    existing_answer.plagiarism_summary = f"High similarity ({int(best_score*100)}%) with student ID {best_match.student_id} on the same question."
                elif best_match and best_score >= 0.7:
                    existing_answer.plagiarism_match_student_id = best_match.student_id
                    existing_answer.plagiarism_match_answer_id = best_match.id
                    existing_answer.plagiarism_summary = f"Notable similarity ({int(best_score*100)}%) with student ID {best_match.student_id}. Review recommended."
                else:
                    existing_answer.plagiarism_match_student_id = None
                    existing_answer.plagiarism_match_answer_id = None
                    existing_answer.plagiarism_summary = None
            except Exception:
                pass
        db.session.add(existing_answer)
        _notify_teacher_quiz_submission(student, question.resource_id)
    else:
        # Create new answer
        student_answer = StudentAnswer(
            student_id=student.id,
            question_id=question_id,
            answer=(answer if answer is not None else ''),
            is_correct=is_correct
        )
        print(f"Creating new answer: {student_answer.answer} for question {question_id}")
        # Compute plagiarism only for essay answers
        if question.question_type == 'essay' and answer and answer.strip():
            try:
                other_answers = StudentAnswer.query.filter(
                    StudentAnswer.question_id == question_id,
                    StudentAnswer.student_id != student.id
                ).all()
                best_score = 0.0
                best_match = None
                for oa in other_answers:
                    s = difflib.SequenceMatcher(None, (answer or '').strip().lower(), (oa.answer or '').strip().lower())
                    score = s.ratio()
                    if score > best_score:
                        best_score = score
                        best_match = oa
                student_answer.plagiarism_score = round(best_score, 4) if best_score else None
                if best_match and best_score >= 0.85:
                    student_answer.plagiarism_match_student_id = best_match.student_id
                    student_answer.plagiarism_match_answer_id = best_match.id
                    student_answer.plagiarism_summary = f"High similarity ({int(best_score*100)}%) with student ID {best_match.student_id} on the same question."
                elif best_match and best_score >= 0.7:
                    student_answer.plagiarism_match_student_id = best_match.student_id
                    student_answer.plagiarism_match_answer_id = best_match.id
                    student_answer.plagiarism_summary = f"Notable similarity ({int(best_score*100)}%) with student ID {best_match.student_id}. Review recommended."
            except Exception:
                pass
        db.session.add(student_answer)
        _notify_teacher_quiz_submission(student, question.resource_id)
    
    try:
        db.session.commit()
        
        # After saving, check if all questions have been answered; count MCQ + essay (essays can be empty but still stored)
        total_questions_all = Question.query.filter_by(resource_id=resource_id).count()
        answered_count = db.session.query(db.func.count(db.func.distinct(StudentAnswer.question_id))).join(Question).filter(
            StudentAnswer.student_id == student.id,
            Question.resource_id == resource_id
        ).scalar() or 0
        if total_questions_all > 0 and answered_count >= total_questions_all:
            # Finalize quiz and return completion payload
            session, ai_recommendation = _finalize_quiz_session(student, resource_id)
            total_mcq = Question.query.filter_by(resource_id=resource_id).filter(Question.question_type == 'mcq').count()
            correct_mcq = StudentAnswer.query.join(Question).filter(
                StudentAnswer.student_id == student.id,
                Question.resource_id == resource_id,
                Question.question_type == 'mcq',
                StudentAnswer.is_correct == True
            ).count()
            return jsonify({
                'success': True,
                'completed': True,
                'final_score': session.quiz_score,
                'correct_answers': correct_mcq,
                'total_questions': total_mcq,
                'ai_recommendation': ai_recommendation
            })
        
        # Commit all changes first
        db.session.commit()
        
        # Then respond with per-answer feedback
        if question.question_type == 'mcq':
            total_questions = Question.query.filter_by(resource_id=resource_id).filter(Question.question_type == 'mcq').count()
            correct_answers = StudentAnswer.query.join(Question).filter(
                StudentAnswer.student_id == student.id,
                Question.resource_id == resource_id,
                Question.question_type == 'mcq',
                StudentAnswer.is_correct == True
            ).count()
            current_score = (correct_answers / total_questions) * 100 if total_questions > 0 else 0
            correct_answer_to_show = None if is_correct else (shuffled_correct or question.correct_answer)
            return jsonify({
                'success': True,
                'is_correct': bool(is_correct),
                'current_score': round(current_score, 1),
                'correct_answers': correct_answers,
                'total_questions': total_questions,
                'correct_answer': correct_answer_to_show
            })
        else:
            return jsonify({'success': True, 'pending_review': True})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error in submit_answer: {str(e)}")
        return jsonify({'success': False, 'error': 'Database error occurred'})

@app.route('/student/complete_quiz/<int:resource_id>', methods=['POST'])
@login_required
def complete_quiz(resource_id):
    # This route is deprecated: quizzes now auto-complete on last answer or timeout
    return jsonify({'success': True, 'message': 'Quiz finalizes automatically after last answer or when time expires.'})

def _finalize_quiz_session(student, resource_id):
    # Get or create latest session
    session = StudySession.query.filter_by(
        student_id=student.id,
        resource_id=resource_id
    ).order_by(StudySession.start_time.desc()).first()
    if not session:
        session = StudySession(
            student_id=student.id,
            resource_id=resource_id,
            start_time=datetime.now()
        )
        db.session.add(session)

    # Check if there are actually questions for this resource
    total_questions = Question.query.filter_by(resource_id=resource_id).count()
    if total_questions == 0:
        # No questions exist - don't mark as completed, just return
        return session, "No quiz available for this resource yet."

    # Update session completion
    session.end_time = datetime.now()
    session.completed = True
    
    # Also mark any other incomplete sessions for this quiz as completed to avoid conflicts
    incomplete_sessions = StudySession.query.filter_by(
        student_id=student.id,
        resource_id=resource_id,
        completed=False
    ).all()
    for incomplete_session in incomplete_sessions:
        if incomplete_session.id != session.id:
            incomplete_session.completed = True
            incomplete_session.end_time = datetime.now()
            if incomplete_session.start_time:
                duration = (incomplete_session.end_time - incomplete_session.start_time).total_seconds()
                incomplete_session.duration = int(duration)

    # Mark reassessment as used if applicable
    reassessment = QuizReassessment.query.filter_by(
        student_id=student.id,
        resource_id=resource_id,
        is_used=False
    ).first()
    if reassessment:
        reassessment.is_used = True
        reassessment.used_at = datetime.now()
        db.session.add(reassessment)

    # Compute MCQ score
    total_mcq = Question.query.filter_by(resource_id=resource_id).filter(Question.question_type == 'mcq').count()
    correct_mcq = StudentAnswer.query.join(Question).filter(
        StudentAnswer.student_id == student.id,
        Question.resource_id == resource_id,
        Question.question_type == 'mcq',
        StudentAnswer.is_correct == True
    ).count()
    session.quiz_score = round(((correct_mcq / total_mcq) * 100), 1) if total_mcq > 0 else 0.0

    # Duration
    if session.start_time:
        duration = (session.end_time - session.start_time).total_seconds()
        session.duration = int(duration)

    # AI recommendation with variety; safely define final_score
    final_score = session.quiz_score or 0.0
    try:
        ai_recommendation = generate_ai_recommendation(session)
        import random
        random.seed(hash(f"{student.id}_{resource_id}_{final_score}") % (2**32))
        encouragement_phrases = [
            "Keep up the amazing work!",
            "You're making great progress!",
            "Your dedication to learning is impressive!",
            "You're on the right track!",
            "Your hard work is paying off!",
            "You're building strong foundations!",
            "Your commitment to improvement is inspiring!",
            "You're developing excellent study habits!"
        ]
        if ai_recommendation and len(ai_recommendation) < 200:
            ai_recommendation += f" {random.choice(encouragement_phrases)}"
        random.seed()
    except Exception:
        import random
        if final_score >= 90:
            variations = [
                f"Outstanding performance! You scored {final_score:.1f}% and have truly mastered this material.",
                f"Exceptional work! Your {final_score:.1f}% score demonstrates excellent understanding.",
                f"Brilliant achievement! You scored {final_score:.1f}% and show mastery of this topic."
            ]
            ai_recommendation = random.choice(variations)
        elif final_score >= 70:
            variations = [
                f"Great job! You scored {final_score:.1f}%. Focus on reviewing missed concepts to strengthen your knowledge.",
                f"Well done! Your {final_score:.1f}% score shows good understanding. Review weak areas for improvement.",
                f"Good effort! You scored {final_score:.1f}%. Practice the concepts you missed to enhance your skills."
            ]
            ai_recommendation = random.choice(variations)
        elif final_score >= 50:
            variations = [
                f"You scored {final_score:.1f}%. Consider reviewing the material and retaking the quiz to improve your understanding.",
                f"Your {final_score:.1f}% score indicates areas for improvement. Review the material thoroughly before retaking.",
                f"You scored {final_score:.1f}%. Focus on understanding the concepts you missed, then try the quiz again."
            ]
            ai_recommendation = random.choice(variations)
        else:
            variations = [
                f"You scored {final_score:.1f}%. Please review the material thoroughly and consider asking your teacher for help.",
                f"Your {final_score:.1f}% score suggests you need more practice. Review fundamentals and seek additional support.",
                f"You scored {final_score:.1f}%. Focus on building a strong foundation before attempting the quiz again."
            ]
            ai_recommendation = random.choice(variations)

    session.ai_recommendation = ai_recommendation
    db.session.commit()
    return session, ai_recommendation

def analyze_content(content, resource_type, num_questions=5):
    """Analyze content and generate questions using OpenAI API (new SDK)"""
    try:
        prompt = (
            f"Generate {num_questions} multiple-choice questions from the following text:\n\n{content}\n\n"
            "Each question should have 4 answer choices (A, B, C, D) and indicate the correct answer."
        )
        print("=== SENDING PROMPT TO OPENAI ===")
        print(prompt)
        completion = client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.7,
        )
        print("=== OPENAI RESPONSE ===")
        print(completion.choices[0].message.content)
        return completion.choices[0].message.content
    except Exception as e:
        print(f"Error in AI analysis: {str(e)}")
        return None

def generate_questions_offline(content: str, num_questions: int = 5):
    """Generate simple MCQ questions without external AI.
    Creates keyword-based questions to ensure the quiz pipeline always works.
    """
    if not content:
        content = "General knowledge"
    # Extract candidate keywords (simple heuristic)
    words = re.findall(r"[A-Za-z]{4,}", content)
    # Count frequency and take unique ordered by appearance
    seen = set()
    keywords = []
    for w in words:
        lw = w.lower()
        if lw not in seen:
            seen.add(lw)
            keywords.append(w)
        if len(keywords) >= 50:
            break
    if not keywords:
        keywords = ["concept", "topic", "principle", "method", "process"]

    def make_question(idx: int, term: str):
        stem = f"Which option best describes the term '{term}' in the context of this resource?"
        # Construct generic distractors
        options = [
            f"A: A key idea related to {term}",
            f"B: An unrelated term not discussed",
            f"C: A minor detail with no significance",
            f"D: A random example without context"
        ]
        # Correct answer is A by construction
        return {
            'question': stem,
            'options': [opt[3:] for opt in options],  # strip letter and colon
            'correct_answer': 'A'
        }

    questions = []
    for i in range(num_questions):
        term = keywords[i % len(keywords)]
        questions.append(make_question(i + 1, term))
    return questions

def generate_ai_recommendation(session):
    """Generate AI recommendation based on student performance (new SDK) with enhanced variety"""
    try:
        # Get performance category based on quiz score
        if session.quiz_score and session.quiz_score >= 90:
            performance_level = "excellent"
        elif session.quiz_score and session.quiz_score >= 80:
            performance_level = "very good"
        elif session.quiz_score and session.quiz_score >= 70:
            performance_level = "good"
        elif session.quiz_score and session.quiz_score >= 60:
            performance_level = "satisfactory"
        elif session.quiz_score and session.quiz_score >= 50:
            performance_level = "needs improvement"
        else:
            performance_level = "requires significant improvement"
        
        # Get study duration in minutes for better context
        duration_minutes = (session.duration or 0) // 60
        
        # Get student name for personalization
        student = Student.query.filter_by(id=session.student_id).first()
        student_name = student.name if student else "Student"
        
        # Add variety to the prompt based on session data
        import random
        random.seed(hash(f"{session.student_id}_{session.resource_id}_{session.quiz_score}") % (2**32))
        
        # Different prompt styles for variety
        prompt_styles = [
            f"""
            As an encouraging educational mentor, provide a personalized recommendation for {student_name}:
            
            Quiz Score: {session.quiz_score}% ({performance_level} performance)
            Study Duration: {duration_minutes} minutes
            Resource: {Resource.query.get(session.resource_id).title}
            
            Give a warm, motivating recommendation (2-3 sentences) that:
            1. Celebrates their {performance_level} performance with specific praise
            2. Provides actionable next steps tailored to their score
            3. Includes a personal touch that makes them feel supported
            """,
            
            f"""
            As a supportive learning coach, analyze this student's performance and provide guidance:
            
            Student: {student_name}
            Performance: {session.quiz_score}% ({performance_level})
            Time Spent: {duration_minutes} minutes
            Topic: {Resource.query.get(session.resource_id).title}
            
            Create an inspiring, personalized recommendation that:
            1. Acknowledges their specific achievements
            2. Offers concrete strategies for improvement or advancement
            3. Motivates them to continue their learning journey
            """,
            
            f"""
            You are a caring educational advisor. Help this student with personalized guidance:
            
            {student_name}'s Results:
            - Score: {session.quiz_score}% ({performance_level})
            - Study Time: {duration_minutes} minutes
            - Subject: {Resource.query.get(session.resource_id).title}
            
            Provide a thoughtful, encouraging recommendation that:
            1. Recognizes their {performance_level} performance
            2. Suggests specific, achievable next steps
            3. Builds their confidence and motivation
            """
        ]
        
        # Select a random prompt style
        selected_prompt = random.choice(prompt_styles)
        
        # Add temperature variation for more diverse responses
        temperature = random.uniform(0.7, 0.9)
        
        completion = client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": "You are a warm, encouraging educational AI assistant that provides personalized, actionable learning recommendations. Always be supportive and motivating."},
                {"role": "user", "content": selected_prompt}
            ],
            temperature=temperature,  # Add randomness to responses
            max_tokens=150  # Keep recommendations concise
        )
        
        random.seed()  # Reset seed
        return completion.choices[0].message.content
        
    except Exception as e:
        print(f"Error generating AI recommendation: {str(e)}")
        # Enhanced fallback recommendations with more variety
        random.seed(hash(f"{session.student_id}_{session.resource_id}") % (2**32))
        
        if session.quiz_score and session.quiz_score >= 80:
            variations = [
                "Excellent work! You've mastered this material. Consider exploring more advanced topics or helping classmates.",
                "Outstanding performance! You're ready to tackle more challenging concepts. Share your knowledge with others!",
                "Brilliant achievement! You've demonstrated deep understanding. Time to explore advanced applications!"
            ]
        elif session.quiz_score and session.quiz_score >= 60:
            variations = [
                "Great effort! Keep up the good work and continue learning.",
                "Well done! Your progress shows dedication. Focus on strengthening your understanding.",
                "Good work! You're building solid foundations. Keep practicing to enhance your skills."
            ]
        else:
            variations = [
                "Keep working hard! Focus on reviewing the fundamental concepts and consider asking your teacher for additional help.",
                "Don't give up! Every challenge is an opportunity to grow. Review the basics and seek support when needed.",
                "Stay motivated! Learning takes time and practice. Focus on understanding the core concepts first."
            ]
        
        result = random.choice(variations)
        random.seed()  # Reset seed
        return result

def generate_teacher_strategy(session):
    """Generate teacher-focused strategy for helping student pass (new function)"""
    try:
        # Get performance category based on quiz score
        if session.quiz_score and session.quiz_score >= 90:
            performance_level = "excellent"
        elif session.quiz_score and session.quiz_score >= 80:
            performance_level = "very good"
        elif session.quiz_score and session.quiz_score >= 70:
            performance_level = "good"
        elif session.quiz_score and session.quiz_score >= 60:
            performance_level = "satisfactory"
        elif session.quiz_score and session.quiz_score >= 50:
            performance_level = "needs improvement"
        else:
            performance_level = "requires significant improvement"
        
        # Get study duration in minutes for better context
        duration_minutes = (session.duration or 0) // 60
        
        # Get student name for personalization
        student = Student.query.filter_by(id=session.student_id).first()
        student_name = student.name if student else "Student"
        
        # Get resource title
        resource = Resource.query.get(session.resource_id)
        resource_title = resource.title if resource else "this topic"
        
        # Create teacher-focused prompts
        prompt_styles = [
            f"""
            As an educational consultant, provide specific strategies for teachers to help {student_name} succeed:
            
            Student: {student_name}
            Current Performance: {session.quiz_score}% ({performance_level})
            Study Time: {duration_minutes} minutes
            Topic: {resource_title}
            
            Provide 2-3 specific, actionable strategies that teachers can implement to:
            1. Help this student improve their understanding
            2. Address specific learning gaps
            3. Provide appropriate support and resources
            4. Monitor progress effectively
            
            Focus on what TEACHERS should do, not what students should do.
            """,
            
            f"""
            You are an experienced teacher mentor. Give practical advice for helping {student_name}:
            
            Performance Data:
            - Score: {session.quiz_score}% ({performance_level})
            - Time Spent: {duration_minutes} minutes
            - Subject: {resource_title}
            
            Provide concrete teaching strategies that:
            1. Target the student's specific performance level
            2. Include specific activities or interventions
            3. Suggest resources or materials to use
            4. Outline how to track improvement
            
            Be specific about TEACHER actions and interventions.
            """,
            
            f"""
            As a learning specialist, recommend teacher interventions for {student_name}:
            
            Assessment Results:
            - Quiz Score: {session.quiz_score}% ({performance_level})
            - Study Duration: {duration_minutes} minutes
            - Learning Area: {resource_title}
            
            Suggest specific teaching strategies that:
            1. Address the student's current performance level
            2. Provide step-by-step intervention plans
            3. Include assessment and monitoring methods
            4. Recommend appropriate learning materials
            
            Focus on TEACHER responsibilities and actions.
            """
        ]
        
        # Select a random prompt style
        import random
        random.seed(hash(f"{session.student_id}_{session.resource_id}_{session.quiz_score}") % (2**32))
        selected_prompt = random.choice(prompt_styles)
        
        # Add temperature variation for more diverse responses
        temperature = random.uniform(0.7, 0.9)
        
        completion = client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": "You are an expert educational consultant that provides specific, actionable strategies for teachers to help students succeed. Focus on what teachers should do, not what students should do."},
                {"role": "user", "content": selected_prompt}
            ],
            temperature=temperature,
            max_tokens=200  # Allow longer strategies for teachers
        )
        
        random.seed()  # Reset seed
        return completion.choices[0].message.content
        
    except Exception as e:
        print(f"Error generating teacher strategy: {str(e)}")
        # Enhanced fallback teacher strategies
        import random
        random.seed(hash(f"{session.student_id}_{session.resource_id}") % (2**32))
        
        if session.quiz_score and session.quiz_score >= 80:
            variations = [
                f"Assign {student_name} to advanced topics and consider having them mentor struggling classmates. Provide enrichment materials and challenge them with complex problem-solving tasks.",
                f"Encourage {student_name} to explore advanced applications of this topic. Assign leadership roles in group activities and provide opportunities for independent research projects.",
                f"Challenge {student_name} with higher-level materials and consider cross-curricular connections. Assign them as a peer tutor and provide advanced assessment opportunities."
            ]
        elif session.quiz_score and session.quiz_score >= 60:
            variations = [
                f"Provide {student_name} with additional practice materials and targeted review sessions. Monitor their progress closely and offer one-on-one support when needed.",
                f"Assign {student_name} supplementary exercises focusing on weak areas. Schedule regular check-ins and provide positive reinforcement for improvements.",
                f"Create a personalized study plan for {student_name} with specific goals and milestones. Offer extra help sessions and encourage peer study groups."
            ]
        else:
            variations = [
                f"Schedule one-on-one tutoring sessions with {student_name} to address fundamental gaps. Provide prerequisite materials and break down complex concepts into smaller steps.",
                f"Create a structured intervention plan for {student_name} with daily check-ins. Assign simpler practice materials and consider peer mentoring from higher-performing students.",
                f"Develop a comprehensive support plan for {student_name} including remedial resources, extra practice time, and regular progress assessments. Consider involving parents in the support process."
            ]
        
        result = random.choice(variations)
        random.seed()  # Reset seed
        return result

def generate_ml_recommendation(session):
    """Generate ML-based recommendation using the ML service"""
    try:
        from ml_service import recommend_for_student
        
        # Prepare study summary for ML analysis
        study_summary = {
            'duration': session.duration or 0,
            'quiz_score': session.quiz_score or 0.0,
            'completed': bool(session.completed)
        }
        
        # Get ML recommendation
        ml_result = recommend_for_student(study_summary)
        
        # Format the ML recommendation
        probability = ml_result.get('success_probability', 0) * 100
        action = ml_result.get('recommended_action', 'practice_related')
        strategy = ml_result.get('strategy', 'Continue practicing')
        confidence = ml_result.get('confidence_level', 'Medium')
        
        # Create a readable ML recommendation
        if action == 'advance':
            ml_rec = f"Success probability: {probability:.1f}% (High confidence). Ready for advanced material. {strategy}"
        elif action == 'practice_related':
            ml_rec = f"Success probability: {probability:.1f}% ({confidence} confidence). Continue with similar practice. {strategy}"
        else:  # review_prerequisites
            ml_rec = f"Success probability: {probability:.1f}% ({confidence} confidence). Focus on fundamentals. {strategy}"
        
        return ml_rec
        
    except Exception as e:
        print(f"Error generating ML recommendation: {str(e)}")
        return None

def extract_text_from_pdf(file_path):
    """Extract text from PDF file"""
    try:
        with open(file_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text()
            return text
    except Exception as e:
        print(f"Error extracting text from PDF: {str(e)}")
        return None

def extract_text_from_docx(file_path):
    """Extract text from DOCX file with recovery for partially corrupted archives.

    Strategy:
    1) Try python-docx normally.
    2) If it fails (e.g., BadZipFile/CRC errors on embedded media), parse word/document.xml directly.
    3) If all fails, return a short fallback message so callers don't crash.
    """
    try:
        doc = docx.Document(file_path)
        text = "\n".join([para.text for para in doc.paragraphs])
        text = text.strip()
        if text:
            return text
    except Exception:
        # Fall back to manual XML extraction; ignore images/media CRC issues
        pass

    # Fallback: read document XML directly to avoid triggering media reads
    try:
        with zipfile.ZipFile(file_path) as zf:
            with zf.open('word/document.xml') as xml_file:
                xml_bytes = xml_file.read()
        # Parse XML and extract text from <w:t> nodes
        try:
            # Namespaces used by DOCX
            ns = {
                'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            }
            root = ET.fromstring(xml_bytes)
            texts = []
            for node in root.findall('.//w:t', ns):
                if node.text:
                    texts.append(node.text)
            combined = " ".join(texts).strip()
            if combined:
                return combined
        except Exception:
            # If XML parsing fails, continue to final fallback
            pass
    except Exception:
        # Could not open as zip or read document.xml; continue to final fallback
        pass

    return "(Unable to extract text from this DOCX. The file may be partially corrupted, but you can still download/view it.)"

@app.route('/teacher/assign_resources', methods=['GET', 'POST'])
@login_required
@teacher_required
def assign_resources():
    # Get teacher's students and resources
    students = Student.query.filter_by(teacher_id=current_user.id).all()
    resources = Resource.query.filter_by(created_by=current_user.id).all()
    
    # Get unique grades from teacher's students
    grades = db.session.query(Student.grade).filter_by(teacher_id=current_user.id).distinct().all()
    grades = [grade[0] for grade in grades]
    grades = sorted(list(set(grades)))  # Deduplicate and sort grades

    selected_grade = None
    filtered_students = students
    if request.method == 'POST':
        selected_grade = request.form.get('filter_grade')
        if selected_grade:
            filtered_students = [s for s in students if s.grade == selected_grade]
    
    # Get current assignments
    assignments = db.session.query(ResourceAssignment, Resource, Student).join(
        Resource, ResourceAssignment.resource_id == Resource.id
    ).join(
        Student, ResourceAssignment.student_id == Student.id
    ).filter(
        Resource.created_by == current_user.id
    ).all()
    
    # Get access data for display
    access_data = []
    for access in ResourceAccess.query.filter_by(created_by=current_user.id).all():
        access_data.append({
            'access': access,
            'resource': Resource.query.get(access.resource_id)
        })
    
    return render_template('assign_resources.html', 
                         students=filtered_students, 
                         resources=resources, 
                         assignments=assignments,
                         access_data=access_data,
                         grades=grades,
                         selected_grade=selected_grade)

def generate_access_key():
    """Generate a unique 8-character access key"""
    alphabet = string.ascii_uppercase + string.digits
    while True:
        key = ''.join(secrets.choice(alphabet) for _ in range(8))
        # Check if key already exists
        if not ResourceAccess.query.filter_by(access_key=key).first():
            return key

@app.route('/teacher/assign_resource', methods=['POST'])
@login_required
@teacher_required
def assign_resource():
    resource_id = request.form.get('resource_id')
    assignment_type = request.form.get('assignment_type', 'individual')  # 'individual' or 'grade'
    student_id = request.form.get('student_id')
    grade = request.form.get('grade')
    max_students_str = request.form.get('max_students', '1')
    max_students = int(max_students_str) if max_students_str.strip() else 1
    generate_key = request.form.get('generate_key') == 'on'
    
    if not resource_id:
        flash('Please select a resource.', 'danger')
        return redirect(url_for('assign_resources'))
    
    # Verify the resource belongs to this teacher
    resource = Resource.query.filter_by(id=resource_id, created_by=current_user.id).first()
    if not resource:
        flash('Resource not found.', 'danger')
        return redirect(url_for('assign_resources'))
    
    if assignment_type == 'individual':
        if not student_id:
            flash('Please select a student.', 'danger')
            return redirect(url_for('assign_resources'))
        
        # Verify the student belongs to this teacher
        student = Student.query.filter_by(id=student_id, teacher_id=current_user.id).first()
        if not student:
            flash('Student not found.', 'danger')
            return redirect(url_for('assign_resources'))
        
        success = assign_resource_to_student(resource, student, max_students, generate_key)
        if success:
            flash(f'Resource "{resource.title}" assigned to {student.name} successfully!', 'success')
    
    elif assignment_type == 'grade':
        if not grade:
            flash('Please select a grade.', 'danger')
            return redirect(url_for('assign_resources'))
        
        # Get all students in the specified grade for this teacher
        students = Student.query.filter_by(teacher_id=current_user.id, grade=grade).all()
        
        if not students:
            flash(f'No students found in Grade {grade}.', 'warning')
            return redirect(url_for('assign_resources'))
        
        # Generate access key if requested
        access_key = None
        if generate_key:
            access_key = generate_access_key()
            
            # Create ResourceAccess entry
            resource_access = ResourceAccess(
                resource_id=resource_id,
                access_key=access_key,
                max_students=max_students,
                created_by=current_user.id
            )
            db.session.add(resource_access)
        
        # Assign to all students in the grade
        assigned_count = 0
        for student in students:
            # Check if assignment already exists
            existing = ResourceAssignment.query.filter_by(
                resource_id=resource_id, 
                student_id=student.id
            ).first()
            
            if not existing:
                assignment = ResourceAssignment(
                    resource_id=resource_id,
                    student_id=student.id,
                    assigned_by=current_user.id,
                    access_key=access_key,
                    max_students=max_students
                )
                db.session.add(assignment)
                assigned_count += 1
        
        try:
            db.session.commit()
            if access_key:
                flash(f'Resource "{resource.title}" assigned to {assigned_count} students in Grade {grade} with access key: {access_key}', 'success')
            else:
                flash(f'Resource "{resource.title}" assigned to {assigned_count} students in Grade {grade} successfully!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error assigning resource: {str(e)}', 'danger')
    
    return redirect(url_for('assign_resources'))

def assign_resource_to_student(resource, student, max_students, generate_key):
    """Helper function to assign a resource to a single student"""
    # Check if assignment already exists
    existing = ResourceAssignment.query.filter_by(
        resource_id=resource.id, 
        student_id=student.id
    ).first()
    
    if existing:
        flash('This resource is already assigned to this student.', 'warning')
        return False
    
    # Generate access key if requested
    access_key = None
    if generate_key:
        access_key = generate_access_key()
        
        # Create ResourceAccess entry
        resource_access = ResourceAccess(
            resource_id=resource.id,
            access_key=access_key,
            max_students=max_students,
            created_by=current_user.id
        )
        db.session.add(resource_access)
    
    # Create assignment
    assignment = ResourceAssignment(
        resource_id=resource.id,
        student_id=student.id,
        assigned_by=current_user.id,
        access_key=access_key,
        max_students=max_students
    )
    
    try:
        db.session.add(assignment)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        flash(f'Error assigning resource: {str(e)}', 'danger')
        return False

@app.route('/teacher/remove_assignment/<int:assignment_id>', methods=['POST'])
@login_required
@teacher_required
def remove_assignment(assignment_id):
    assignment = ResourceAssignment.query.get_or_404(assignment_id)
    
    # Verify the assignment belongs to this teacher's resource
    resource = Resource.query.filter_by(id=assignment.resource_id, created_by=current_user.id).first()
    if not resource:
        flash('Assignment not found.', 'danger')
        return redirect(url_for('assign_resources'))
    
    try:
        db.session.delete(assignment)
        db.session.commit()
        flash('Assignment removed successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error removing assignment: {str(e)}', 'danger')
    
    return redirect(url_for('assign_resources'))

@app.route('/student/access_with_key', methods=['GET', 'POST'])
@login_required
def access_with_key():
    if current_user.role != 'student':
        abort(403)
    
    if request.method == 'POST':
        access_key = request.form.get('access_key')
        
        if not access_key:
            flash('Please enter an access key.', 'danger')
            return render_template('access_with_key.html')
        
        # Find the resource access entry
        resource_access = ResourceAccess.query.filter_by(access_key=access_key, is_active=True).first()
        
        if not resource_access:
            flash('Invalid access key.', 'danger')
            return render_template('access_with_key.html')
        
        # Check if the resource exists and is accessible
        resource = Resource.query.get(resource_access.resource_id)
        if not resource:
            flash('Resource not found.', 'danger')
            return render_template('access_with_key.html')
        
        # Check if student already has access
        student = Student.query.filter_by(user_id=current_user.id).first()
        if not student:
            flash('Student profile not found.', 'danger')
            return render_template('access_with_key.html')
        
        existing_assignment = ResourceAssignment.query.filter_by(
            resource_id=resource.id,
            student_id=student.id
        ).first()
        
        if existing_assignment:
            flash('You already have access to this resource.', 'info')
            return redirect(url_for('view_resource', resource_id=resource.id))
        
        # Check if the access key has reached its limit
        if resource_access.current_usage >= resource_access.max_students:
            flash('This access key has reached its maximum usage limit.', 'danger')
            return render_template('access_with_key.html')
        
        # Create assignment for this student
        assignment = ResourceAssignment(
            resource_id=resource.id,
            student_id=student.id,
            assigned_by=resource_access.created_by,
            access_key=access_key,
            max_students=resource_access.max_students
        )
        
        # Update usage count
        resource_access.current_usage += 1
        
        try:
            db.session.add(assignment)
            db.session.commit()
            flash(f'Access granted to "{resource.title}"!', 'success')
            return redirect(url_for('view_resource', resource_id=resource.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error accessing resource: {str(e)}', 'danger')
    
    return render_template('access_with_key.html')

@app.route('/teacher/manage_access_keys')
@login_required
@teacher_required
def manage_access_keys():
    # Get all access keys created by this teacher
    access_keys = ResourceAccess.query.filter_by(created_by=current_user.id).all()
    
    # Get resource details for each access key
    access_data = []
    for access in access_keys:
        resource = Resource.query.get(access.resource_id)
        assignments = ResourceAssignment.query.filter_by(access_key=access.access_key).all()
        students = [Student.query.get(assignment.student_id) for assignment in assignments]
        
        access_data.append({
            'access': access,
            'resource': resource,
            'students': students
        })
    
    return render_template('manage_access_keys.html', access_data=access_data)

@app.route('/teacher/revoke_access_key/<int:access_id>', methods=['POST'])
@login_required
@teacher_required
def revoke_access_key(access_id):
    access = ResourceAccess.query.get_or_404(access_id)
    
    # Verify the access key belongs to this teacher
    if access.created_by != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('manage_access_keys'))
    
    try:
        # Deactivate the access key
        access.is_active = False
        
        # Also deactivate all assignments using this key
        assignments = ResourceAssignment.query.filter_by(access_key=access.access_key).all()
        for assignment in assignments:
            assignment.is_active = False
        
        db.session.commit()
        flash(f'Access key {access.access_key} has been revoked.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error revoking access key: {str(e)}', 'danger')
    
    return redirect(url_for('manage_access_keys'))

@app.route('/teacher/delete_resource/<int:resource_id>', methods=['POST'])
@login_required
@teacher_required
def delete_resource(resource_id):
    # Validate CSRF token using Flask-WTF
    from flask_wtf.csrf import validate_csrf
    try:
        validate_csrf(request.form.get('csrf_token'))
    except Exception as e:
        flash('Invalid CSRF token. Please try again.', 'danger')
        return redirect(url_for('teacher_resources'))
    
    resource = Resource.query.filter_by(id=resource_id, created_by=current_user.id).first()
    if not resource:
        flash('Resource not found or access denied.', 'danger')
        return redirect(url_for('teacher_resources'))
    try:
        # Soft delete: preserve student data and related records (assignments, questions, sessions, answers)
        resource.is_deleted = True
        resource.deleted_at = datetime.now()
        resource.deleted_by = current_user.id
        db.session.commit()
        flash('Resource deleted successfully! Student records have been preserved.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting resource: {str(e)}', 'danger')
    return redirect(url_for('teacher_resources'))

@app.route('/teacher/grade_student_notes/<int:notes_id>', methods=['POST'])
@login_required
@teacher_required
def grade_student_notes(notes_id):
    """Grade student notes"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        grade = data.get('grade')
        feedback = data.get('feedback', '')
        
        if grade is None:
            return jsonify({'success': False, 'error': 'Grade is required'}), 400
        
        # Get the notes record
        notes = StudentNotes.query.get_or_404(notes_id)
        
        # Verify teacher has access to this student's notes
        student = Student.query.get(notes.student_id)
        if not student or student.teacher_id != current_user.id:
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Update the notes with grade and feedback
        notes.teacher_grade = float(grade)
        notes.teacher_feedback = feedback
        notes.graded_at = datetime.now()
        notes.graded_by = current_user.id
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Notes graded successfully',
            'grade': notes.teacher_grade,
            'feedback': notes.teacher_feedback
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/teacher/test_email')
@login_required
@teacher_required
def test_email():
    """Test email sending functionality"""
    try:
        # Test sending email to student
        student_email = "chrisuwizeyi045@gmail.com"
        subject = "Test Email from Student Tracking System"
        body = f"Hello,\n\nThis is a test email from the Student Tracking System.\n\nIf you receive this, email notifications are working correctly!\n\nRegards,\nStudent Tracking System"
        
        result = send_email(student_email, subject, body)
        if result:
            flash(f'Test email sent successfully to {student_email}!', 'success')
        else:
            flash(f'Failed to send test email to {student_email}. Check SMTP configuration.', 'danger')
    except Exception as e:
        flash(f'Error sending test email: {str(e)}', 'danger')
    
    return redirect(url_for('teacher_resources'))

@app.route('/teacher/test_email_original')
@login_required
@teacher_required
def test_email_original():
    """Send a test email to verify SMTP configuration.
    Usage: /teacher/test_email?to=someone@example.com
    If 'to' not provided, falls back to current user's email, then SMTP_USER.
    """
    to_address = request.args.get('to')
    if not to_address:
        # Try current user's email
        user = User.query.get(current_user.id)
        if user and getattr(user, 'email', None):
            to_address = user.email
        elif SMTP_USER:
            to_address = SMTP_USER
    if not to_address:
        return jsonify({'success': False, 'error': 'No recipient email found. Provide ?to=... or set your user email.'}), 400
    ok = send_email(to_address, 'Student Tracker SMTP Test', 'If you received this, your SMTP settings are working.')
    if ok:
        return jsonify({'success': True, 'to': to_address})
    return jsonify({'success': False, 'to': to_address, 'error': 'Send failed. Check SMTP_* values and credentials.'}), 500

@app.route('/resource/inline/<int:resource_id>', endpoint='serve_resource_inline_view')
@login_required
def serve_resource_inline_view(resource_id):
    """Serve resource content inline for viewing within an iframe.

    - PDF and video files are streamed inline with appropriate MIME types
    - DOCX files are rendered as a simple HTML preview using extracted text
    """
    resource = Resource.query.get_or_404(resource_id)

    # Only students assigned to the resource (or with public access) can view
    if current_user.role != 'student':
        abort(403)
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    # Allow access if student has an assignment OR if class match and resource has no active keys
    assignment = ResourceAssignment.query.filter_by(
        resource_id=resource_id,
        student_id=student.id
    ).first()
    class_match = (resource.created_by == student.teacher_id and resource.grade == student.grade)
    if assignment:
        has_access = True
    else:
        access_keys_count = ResourceAccess.query.filter_by(resource_id=resource_id, is_active=True).count()
        # If there are no active keys, class match implies open-to-class viewing
        has_access = class_match and access_keys_count == 0
    if not has_access:
        abort(403)

    if not resource.file_path:
        abort(404)

    rel_path = resource.file_path.replace('\\', '/')
    abs_path = os.path.join(app.root_path, 'static', rel_path)
    if not os.path.exists(abs_path):
        abort(404)

    # Decide how to serve based on file extension
    lower_path = rel_path.lower()
    try:
        if lower_path.endswith('.pdf'):
            return send_file(abs_path, mimetype='application/pdf', as_attachment=False, download_name=os.path.basename(abs_path))
        if lower_path.endswith('.mp4'):
            return send_file(abs_path, mimetype='video/mp4', as_attachment=False, download_name=os.path.basename(abs_path))
        if lower_path.endswith('.webm'):
            return send_file(abs_path, mimetype='video/webm', as_attachment=False, download_name=os.path.basename(abs_path))
        if lower_path.endswith('.ogg') or lower_path.endswith('.ogv'):
            return send_file(abs_path, mimetype='video/ogg', as_attachment=False, download_name=os.path.basename(abs_path))
        if lower_path.endswith('.docx'):
            # Render a basic HTML preview for DOCX content to avoid forced download
            extracted = extract_text_from_docx(abs_path) or ''
            html = f"""
<!DOCTYPE html>
<html>
  <head>
    <meta charset=\"utf-8\">
    <title>{resource.title}</title>
    <style>
      body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; line-height: 1.6; padding: 1rem; }}
      pre {{ white-space: pre-wrap; word-wrap: break-word; }}
    </style>
  </head>
  <body>
    <h3>{resource.title}</h3>
    <pre>{extracted}</pre>
  </body>
</html>
"""
            return html
        # Default: try to serve inline with detected type; avoid attachment
        guessed_type = mimetypes.guess_type(abs_path)[0] or 'application/octet-stream'
        return send_file(abs_path, mimetype=guessed_type, as_attachment=False, download_name=os.path.basename(abs_path))
    except Exception as e:
        # If anything goes wrong, avoid download and show a minimal error page inline
        fallback = f"""
<!DOCTYPE html>
<html><head><meta charset=\"utf-8\"><title>{resource.title}</title></head>
<body>
  <p>Unable to display this resource inline. You may try downloading it from your resources list.</p>
  <p style=\"color:#888\">Error: {str(e)}</p>
</body></html>
"""
        return fallback

@app.route('/download/<path:filename>')
@login_required
def download_file(filename):
    # Serve only files within the static directory
    safe_rel_path = os.path.normpath(filename).replace('\\', '/')
    if '..' in safe_rel_path or safe_rel_path.startswith('/'):
        abort(400)
    static_dir = os.path.join(app.root_path, 'static')
    return send_from_directory(static_dir, safe_rel_path, as_attachment=True)

@app.route('/ml/train', methods=['POST'])
@login_required
@teacher_required
def ml_train():
    # Gather study sessions for this teacher's students
    teacher_students = Student.query.filter_by(teacher_id=current_user.id).all()
    student_ids = [s.id for s in teacher_students]
    sessions = StudySession.query.filter(StudySession.student_id.in_(student_ids)).all()
    payload = []
    for s in sessions:
        payload.append({
            'duration': s.duration or 0,
            'quiz_score': s.quiz_score or 0.0,
            'completed': bool(s.completed),
            'resource_id': s.resource_id,
            'student_id': s.student_id,
        })
    result = ml_train_model(payload)
    return jsonify(result)

@app.route('/teacher/insights')
@login_required
@teacher_required
def teacher_insights():
    # Aggregate metrics and recommendations per student
    teacher_students = Student.query.filter_by(teacher_id=current_user.id).all()
    insights = []
    
    # Check if ML model needs training
    from ml_service import get_model_info
    model_info = get_model_info()
    
    # If model is not trained or has insufficient data, train it first
    if model_info.get('status') != 'trained':
        try:
            print("ML model not trained, attempting to train with available data...")
            # Gather all study sessions for training
            all_sessions = StudySession.query.filter(StudySession.quiz_score.isnot(None)).all()
            if len(all_sessions) >= 5:  # Need at least 5 sessions to train
                payload = []
                for s in all_sessions:
                    payload.append({
                        'duration': s.duration or 0,
                        'quiz_score': s.quiz_score or 0.0,
                        'completed': bool(s.completed),
                        'resource_id': s.resource_id,
                        'student_id': s.student_id,
                    })
                ml_train_model(payload)
                print("ML model trained successfully!")
            else:
                print(f"Insufficient data for training. Need at least 5 sessions, got {len(all_sessions)}")
        except Exception as e:
            print(f"Error training ML model: {str(e)}")
    for st in teacher_students:
        # Latest session for quick summary
        last_session = StudySession.query.filter_by(student_id=st.id).order_by(StudySession.end_time.desc()).first()
        if last_session:
            try:
                # First, get ML-based success probability using the trained model
                summary = {
                    'duration': last_session.duration or 0,
                    'quiz_score': last_session.quiz_score or 0.0,
                    'completed': bool(last_session.completed),
                }
                ml_rec = ml_recommend(summary)
                
                # Get teacher-focused AI strategy
                teacher_strategy = generate_teacher_strategy(last_session)
                if teacher_strategy:
                    # Use ML prediction for action and probability, but AI strategy for teacher actions
                    success_prob = ml_rec.get('success_probability', 0)
                    action = ml_rec.get('recommended_action', 'unknown')
                    strategy = teacher_strategy
                    
                    rec = {
                        'success_probability': success_prob,
                        'recommended_action': action,
                        'strategy': strategy
                    }
                else:
                    # Fallback to ML recommendation if AI strategy fails
                    rec = ml_rec
            except Exception as e:
                print(f"Error generating teacher strategy for teacher insights: {str(e)}")
                # Fallback to ML recommendation
                summary = {
                    'duration': last_session.duration or 0,
                    'quiz_score': last_session.quiz_score or 0.0,
                    'completed': bool(last_session.completed),
                }
                rec = ml_recommend(summary)
        else:
            rec = {'success_probability': None, 'recommended_action': 'insufficient_data', 'strategy': 'Encourage student to start a study session.'}
        insights.append({
            'student': st,
            'recommendation': rec,
        })
    # Overall distribution for quick view
    student_ids = [s.id for s in teacher_students]
    total_sessions = StudySession.query.filter(StudySession.student_id.in_(student_ids)).count()
    completed_sessions = StudySession.query.filter(StudySession.student_id.in_(student_ids), StudySession.completed == True).count()
    avg_score = db.session.query(db.func.avg(StudySession.quiz_score)).filter(StudySession.student_id.in_(student_ids)).scalar()
    return render_template('teacher_insights.html', 
                         insights=insights, 
                         total_sessions=total_sessions, 
                         completed_sessions=completed_sessions, 
                         avg_score=avg_score or 0,
                         model_info=model_info)

@app.route('/teacher/resource/<int:resource_id>/suggest_assignments', methods=['POST'])
@login_required
@teacher_required
def suggest_assignments_for_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    if resource.created_by != current_user.id:
        abort(403)

    # Candidate students: teacher's students in the resource grade
    students = Student.query.filter_by(teacher_id=current_user.id, grade=resource.grade).all()
    suggestions = []
    for st in students:
        # Prefer last session on this resource, else last session overall
        last_on_resource = StudySession.query.filter_by(student_id=st.id, resource_id=resource_id).order_by(StudySession.end_time.desc()).first()
        last_any = StudySession.query.filter_by(student_id=st.id).order_by(StudySession.end_time.desc()).first()
        base = last_on_resource or last_any
        if base:
            try:
                # First, get ML-based success probability using the trained model
                summary = {
                    'duration': (base.duration if base and base.duration else 0),
                    'quiz_score': (base.quiz_score if base and base.quiz_score is not None else 0.0),
                    'completed': bool(base.completed) if base else False,
                }
                ml_rec = ml_recommend(summary)
                prob = ml_rec.get('success_probability', 0)
                
                # Get teacher-focused AI strategy
                teacher_strategy = generate_teacher_strategy(base)
                if teacher_strategy:
                    strategy = teacher_strategy
                else:
                    strategy = ml_rec.get('strategy', 'No specific strategy available')
            except Exception as e:
                print(f"Error generating teacher strategy for assignment suggestion: {str(e)}")
                # Fallback to ML recommendation
                summary = {
                    'duration': (base.duration if base and base.duration else 0),
                    'quiz_score': (base.quiz_score if base and base.quiz_score is not None else 0.0),
                    'completed': bool(base.completed) if base else False,
                }
                rec = ml_recommend(summary)
                prob = rec.get('success_probability', 0)
                strategy = rec.get('strategy', 'No specific strategy available')
        else:
            prob = 0.0
            strategy = "No previous performance data available"
        
        # Heuristic: suggest assignment for mid/high readiness
        should_assign = prob is not None and 0.5 <= float(prob) <= 0.85
        if should_assign:
            suggestions.append({
                'student_id': st.id,
                'student_name': st.name,
                'probability': float(prob),
                'strategy': strategy,
            })

    # Sort by probability descending and cap list size
    suggestions.sort(key=lambda x: x['probability'], reverse=True)
    suggestions = suggestions[:25]
    return jsonify({'resource_id': resource_id, 'suggestions': suggestions, 'count': len(suggestions)})


def _train_global_model_once():
    # Train on all sessions (global model)
    sessions = StudySession.query.all()
    payload = []
    for s in sessions:
        payload.append({
            'duration': s.duration or 0,
            'quiz_score': s.quiz_score or 0.0,
            'completed': bool(s.completed),
            'resource_id': s.resource_id,
            'student_id': s.student_id,
        })
    try:
        ml_train_model(payload)
    except Exception as e:
        print(f"Nightly training error: {e}")


def _seconds_until(hour: int, minute: int = 0) -> int:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return int((target - now).total_seconds())


def start_nightly_trainer():
    def worker():
        with app.app_context():
            # Initial delay to next 02:00 local time
            time.sleep(max(1, _seconds_until(2, 0)))
            while True:
                _train_global_model_once()
                # Sleep ~24h until next 02:00
                time.sleep(max(3600, _seconds_until(2, 0)))
    t = threading.Thread(target=worker, daemon=True)
    t.start()

@app.route('/teacher/mark_essays/<int:resource_id>')
@login_required
@teacher_required
def mark_essays(resource_id):
    """Interface for teachers to mark essay questions"""
    resource = Resource.query.get_or_404(resource_id)
    if resource.resource_type != 'quiz':
        flash('This is not a quiz resource.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    
    # Get all essay questions for this quiz
    essay_questions = Question.query.filter_by(resource_id=resource_id, question_type='essay').all()
    
    # Get all student answers for essay questions
    essay_answers = []
    for question in essay_questions:
        answers = StudentAnswer.query.filter_by(question_id=question.id).all()
        for answer in answers:
            student = Student.query.get(answer.student_id)
            essay_answers.append({
                'answer': answer,
                'question': question,
                'student': student
            })
    
    return render_template('mark_essays.html', 
                         resource=resource, 
                         essay_answers=essay_answers)

@app.route('/teacher/mark_quiz/<int:resource_id>')
@login_required
@teacher_required
def mark_quiz(resource_id):
    """Interface for teachers to mark all quiz questions (MCQ and Essay)"""
    resource = Resource.query.get_or_404(resource_id)
    if resource.resource_type != 'quiz':
        flash('This is not a quiz resource.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    
    # Get all questions for this quiz
    questions = Question.query.filter_by(resource_id=resource_id).order_by(Question.id).all()
    
    if not questions:
        flash('No questions found for this quiz.', 'warning')
        return redirect(url_for('quiz_results', quiz_id=resource_id))
    
    # Get all students who have any answers for this quiz (even if session not marked completed)
    student_ids = [sid for (sid,) in db.session.query(StudentAnswer.student_id).join(Question, StudentAnswer.question_id == Question.id).filter(Question.resource_id == resource_id).distinct().all()]
    # Fallback: also include completed sessions
    if not student_ids:
        student_sessions = StudySession.query.filter_by(resource_id=resource_id, completed=True).all()
        student_ids = [session.student_id for session in student_sessions]
    
    if not student_ids:
        return render_template('mark_quiz.html', 
                           resource=resource, 
                           questions=[],
                           student_answers={},
                           student_info={})
    
    # Get all student answers for this quiz
    all_answers = StudentAnswer.query.join(Question).filter(
        Question.resource_id == resource_id,
        StudentAnswer.student_id.in_(student_ids) if student_ids else False
    ).all()
    
    # Organize answers by student and question
    student_answers = {}
    for answer in all_answers:
        if answer.student_id not in student_answers:
            student_answers[answer.student_id] = {}
        student_answers[answer.student_id][answer.question_id] = answer
    
    # Get student information
    students = Student.query.filter(Student.id.in_(student_ids)).all()
    student_info = {student.id: student for student in students}
    
    return render_template('mark_quiz.html', 
                         resource=resource, 
                         questions=questions,
                         student_answers=student_answers,
                         student_info=student_info)

@app.route('/teacher/grade_essay', methods=['POST'])
@login_required
@teacher_required
def grade_essay():
    """Grade a specific essay answer"""
    answer_id = request.form.get('answer_id')
    marks_awarded = request.form.get('marks_awarded')
    feedback = request.form.get('feedback', '')
    
    if not all([answer_id, marks_awarded]):
        flash('Missing required fields.', 'danger')
        return redirect(request.referrer or url_for('teacher_dashboard'))
    
    try:
        answer = StudentAnswer.query.get_or_404(answer_id)
        question = Question.query.get(answer.question_id)
        
        # Validate marks awarded
        marks_awarded = float(marks_awarded)
        if marks_awarded < 0 or marks_awarded > question.marks:
            flash(f'Marks must be between 0 and {question.marks}.', 'danger')
            return redirect(request.referrer)
        
        # Update the answer
        answer.marks_awarded = marks_awarded
        answer.teacher_feedback = feedback
        answer.graded_at = datetime.now()
        answer.is_correct = marks_awarded > 0  # Consider it correct if any marks awarded
        
        # Find the study session for this attempt
        study_session = StudySession.query.filter_by(
            student_id=answer.student_id,
            resource_id=Question.query.get(answer.question_id).resource_id
        ).order_by(StudySession.start_time.desc()).first()
        
        if study_session:
            # Recalculate total score for the quiz
            total_marks = 0
            max_marks = 0
            
            # Get all answers for this session
            answers = db.session.query(
                StudentAnswer, Question
            ).join(
                Question, StudentAnswer.question_id == Question.id
            ).filter(
                StudentAnswer.student_id == answer.student_id,
                Question.resource_id == study_session.resource_id
            ).all()
            
            for ans, q in answers:
                max_marks += q.marks
                if ans.marks_awarded is not None:
                    total_marks += ans.marks_awarded
            
            # Update quiz score as percentage but don't mark as published
            if max_marks > 0:
                study_session.quiz_score = (total_marks / max_marks) * 100
                # Don't commit yet, we'll do it after updating metadata
        
        db.session.commit()
        flash('Essay graded successfully! Marks will be visible to students after publishing.', 'success')
        
    except ValueError:
        flash('Invalid marks value.', 'danger')
    except Exception as e:
        flash(f'Error grading essay: {str(e)}', 'danger')
        db.session.rollback()
    
    return redirect(request.referrer or url_for('teacher_dashboard'))

@app.route('/teacher/fix_quiz_metadata/<int:resource_id>')
@login_required
@teacher_required
def fix_quiz_metadata(resource_id):
    """Fix missing quiz metadata for existing quizzes"""
    resource = Resource.query.get_or_404(resource_id)
    if resource.resource_type != 'quiz':
        flash('This is not a quiz resource.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    
    # Check if metadata exists
    metadata = QuizMetadata.query.filter_by(resource_id=resource_id).first()
    if not metadata:
        # Create metadata
        metadata = QuizMetadata(
            resource_id=resource_id,
            time_limit=None,
            passing_score=70,
            created_by=current_user.id,
            marks_published=False
        )
        db.session.add(metadata)
        db.session.commit()
        flash('Quiz metadata created successfully!', 'success')
    else:
        flash('Quiz metadata already exists.', 'info')
    
    return redirect(url_for('quiz_results', quiz_id=resource_id))

@app.route('/teacher/publish_marks/<int:resource_id>', methods=['POST'])
@login_required
@teacher_required
def publish_marks(resource_id):
    """Publish marks for a quiz to students"""
    resource = Resource.query.get_or_404(resource_id)
    if resource.resource_type != 'quiz':
        flash('This is not a quiz resource.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    
    # Get or create quiz metadata
    metadata = QuizMetadata.query.filter_by(resource_id=resource_id).first()
    if not metadata:
        # Create metadata if it doesn't exist (for older quizzes)
        metadata = QuizMetadata(
            resource_id=resource_id,
            time_limit=None,
            passing_score=70,
            created_by=current_user.id,
            marks_published=False
        )
        db.session.add(metadata)
        db.session.flush()  # Flush to get the ID
    
    # Update quiz metadata to mark as published
    metadata.marks_published = True
    metadata.marks_published_at = datetime.now()
    db.session.commit()
    flash('Marks published successfully! Students can now view their results.', 'success')

    # Notify students and teacher
    try:
        # All students who attempted this quiz (have answers or a completed session)
        student_ids = set([s.student_id for s in StudySession.query.filter_by(resource_id=resource_id, completed=True).all()])
        answer_students = set([a.student_id for a in StudentAnswer.query.join(Question, StudentAnswer.question_id == Question.id).filter(Question.resource_id == resource_id).all()])
        all_students = student_ids.union(answer_students)
        for sid in all_students:
            sn = StudentNotification(
                student_id=sid,
                resource_id=resource_id,
                title='Marks Published',
                message='Your quiz marks have been published. You can now view detailed results.',
                is_read=False
            )
            db.session.add(sn)
        tn = TeacherNotification(
            teacher_id=current_user.id,
            student_id=0,
            resource_id=resource_id,
            notification_type='marks_published',
            title='Marks Published',
            message='Marks have been published to all students for this quiz.',
            severity='info',
            is_read=False
        )
        db.session.add(tn)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Failed to create publish notifications: {e}")
    
    return redirect(url_for('quiz_results', quiz_id=resource_id))

@app.route('/student/my_marks/<int:resource_id>')
@login_required
def student_my_marks(resource_id):
    """Student view of their own detailed marks for a quiz"""
    if current_user.role != 'student':
        abort(403)
    
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        abort(404)
    
    resource = Resource.query.get_or_404(resource_id)
    if resource.resource_type != 'quiz':
        flash('This is not a quiz resource.', 'danger')
        return redirect(url_for('student_dashboard'))
    
    # Check if marks are published
    metadata = QuizMetadata.query.filter_by(resource_id=resource_id).first()
    if not metadata or not metadata.marks_published:
        flash('Marks for this quiz have not been published yet.', 'warning')
        return redirect(url_for('student_dashboard'))
    
    # Get the student's session for this quiz
    session = StudySession.query.filter_by(
        student_id=student.id, 
        resource_id=resource_id
    ).first()
    
    if not session:
        flash('No quiz attempt found.', 'danger')
        return redirect(url_for('student_dashboard'))
    
    # Get all questions for this quiz
    questions = Question.query.filter_by(resource_id=resource_id).all()
    
    # Get student answers for each question
    student_answers = {}
    for question in questions:
        answer = StudentAnswer.query.filter_by(
            student_id=student.id,
            question_id=question.id
        ).first()
        if answer:
            student_answers[question.id] = answer
    
    # Calculate total possible marks
    total_marks = sum(q.marks for q in questions)
    
    # Calculate earned marks
    earned_marks = 0
    for question in questions:
        if question.id in student_answers:
            answer = student_answers[question.id]
            if question.question_type == 'mcq':
                # For MCQ, check if answer is correct
                if answer.is_correct:
                    earned_marks += question.marks
            else:
                # For essay, use marks_awarded if available
                if answer.marks_awarded is not None:
                    earned_marks += answer.marks_awarded
    
    return render_template('student_my_marks.html',
                         resource=resource,
                         student=student,
                         session=session,
                         questions=questions,
                         student_answers=student_answers,
                         total_marks=total_marks,
                         earned_marks=earned_marks)

@app.route('/teacher/student_marks/<int:resource_id>/<int:student_id>')
@login_required
@teacher_required
def view_student_marks(resource_id, student_id):
    """View detailed marks for a specific student's quiz attempt"""
    resource = Resource.query.get_or_404(resource_id)
    student = Student.query.get_or_404(student_id)
    
    if resource.resource_type != 'quiz':
        flash('This is not a quiz resource.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    
    # Get the student's session for this quiz
    session = StudySession.query.filter_by(
        student_id=student_id, 
        resource_id=resource_id
    ).first()
    
    if not session:
        flash('No quiz attempt found for this student.', 'danger')
        return redirect(url_for('quiz_results', quiz_id=resource_id))
    
    # Get all questions for this quiz
    questions = Question.query.filter_by(resource_id=resource_id).all()
    
    # Get student answers for each question
    student_answers = {}
    for question in questions:
        answer = StudentAnswer.query.filter_by(
            student_id=student_id,
            question_id=question.id
        ).first()
        if answer:
            student_answers[question.id] = answer
            print(f"Found answer for question {question.id}: {answer.answer}")
        else:
            print(f"No answer found for question {question.id} by student {student_id}")
    
    print(f"Total questions: {len(questions)}")
    print(f"Total answers found: {len(student_answers)}")
    print(f"Student answers: {student_answers}")
    
    # Also check if there are any answers for this student in this resource at all
    all_student_answers = StudentAnswer.query.join(Question).filter(
        StudentAnswer.student_id == student_id,
        Question.resource_id == resource_id
    ).all()
    print(f"All answers for student {student_id} in resource {resource_id}: {len(all_student_answers)}")
    for ans in all_student_answers:
        print(f"  Answer ID {ans.id}: Question {ans.question_id}, Answer: '{ans.answer}', Correct: {ans.is_correct}")
    
    # If we found answers through the join but not through direct query, use them
    if len(all_student_answers) > len(student_answers):
        print("Using answers found through join query")
        for ans in all_student_answers:
            if ans.question_id not in student_answers:
                student_answers[ans.question_id] = ans
    
    # Calculate total possible marks
    total_marks = sum(q.marks for q in questions)
    
    # Calculate earned marks
    earned_marks = 0
    for question in questions:
        if question.id in student_answers:
            answer = student_answers[question.id]
            if question.question_type == 'mcq':
                # For MCQ, check if answer is correct
                if answer.is_correct:
                    earned_marks += question.marks
            else:
                # For essay, use marks_awarded if available
                if answer.marks_awarded is not None:
                    earned_marks += answer.marks_awarded
    
    return render_template('student_marks_detail.html',
                         resource=resource,
                         student=student,
                         session=session,
                         questions=questions,
                         student_answers=student_answers,
                         total_marks=total_marks,
                         earned_marks=earned_marks)

@app.route('/teacher/create_quiz', methods=['GET', 'POST'])
@login_required
@teacher_required
def create_quiz():
    # Optional resource_id to attach questions to an existing resource (note/video/link)
    resource_id = request.args.get('resource_id') or request.form.get('resource_id')
    if request.method == 'POST':
        # Verify CSRF token
        if not request.form.get('csrf_token') or request.form.get('csrf_token') != csrf._get_csrf_token():
            abort(400, 'Invalid CSRF token')
            
        title = request.form.get('title')
        description = request.form.get('description')
        grade = request.form.get('grade')
        time_limit = request.form.get('time_limit')
        passing_score = request.form.get('passing_score')
        questions_data = request.form.get('questions_data')
        
        if not all([title, grade, questions_data]):
            flash('Required fields are missing!', 'danger')
            return redirect(url_for('create_quiz'))
        
        try:
            # If resource_id provided, attach questions to that resource, else create a quiz resource
            if resource_id:
                quiz_resource = Resource.query.get_or_404(int(resource_id))
                # Ensure ownership and grade
                if quiz_resource.created_by != current_user.id:
                    abort(403)
                # Do NOT convert original resource to 'quiz'; keep it visible under Resources.
                # Update metadata title/description if provided
                if title:
                    quiz_resource.title = title
                if description:
                    quiz_resource.description = description
                if grade:
                    quiz_resource.grade = grade
                db.session.flush()
            else:
                quiz_resource = Resource(
                    title=title,
                    description=description or f"Quiz: {title}",
                    resource_type='quiz',
                    created_by=current_user.id,
                    grade=grade
                )
                db.session.add(quiz_resource)
                db.session.flush()
            
            # Parse and save questions
            questions = json.loads(questions_data)
            for q in questions:
                q_type = q.get('question_type', 'mcq')
                options = q.get('options') if q_type == 'mcq' else None
                correct_answer = q.get('correct_answer') if q_type == 'mcq' else None
                marks = q.get('marks', 1)  # Default to 1 mark if not specified
                question = Question(
                    resource_id=quiz_resource.id,
                    question_text=q['question'],
                    correct_answer=correct_answer,
                    options=options,
                    question_type=q_type,
                    marks=marks
                )
                db.session.add(question)
            
            # Save quiz metadata - check if already exists first
            # Convert time_limit from minutes to seconds
            time_limit_seconds = None
            if time_limit and time_limit.strip():
                try:
                    time_limit_seconds = int(time_limit) * 60  # Convert minutes to seconds
                except ValueError:
                    time_limit_seconds = None
            
            # Check if metadata already exists for this resource
            existing_metadata = QuizMetadata.query.filter_by(resource_id=quiz_resource.id).first()
            if existing_metadata:
                # Update existing metadata
                existing_metadata.time_limit = time_limit_seconds
                existing_metadata.passing_score = int(passing_score) if passing_score else 70
                existing_metadata.created_by = current_user.id
            else:
                # Create new metadata
                quiz_metadata = QuizMetadata(
                    resource_id=quiz_resource.id,
                    time_limit=time_limit_seconds,
                    passing_score=int(passing_score) if passing_score else 70,
                    created_by=current_user.id
                )
                db.session.add(quiz_metadata)
            
            db.session.commit()
            try:
                trigger_resource_notification_async(quiz_resource.id)
            except Exception:
                pass
            flash('Quiz created successfully!', 'success')
            # Redirect to view the specific quiz that was just created
            return redirect(url_for('quiz_results', quiz_id=quiz_resource.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating quiz: {str(e)}', 'danger')
            return redirect(url_for('create_quiz'))
    
    # Get grades this teacher teaches
    teacher_students = Student.query.filter_by(teacher_id=current_user.id).all()
    teacher_grades = sorted(list(set([s.grade for s in teacher_students])))
    # If coming from resource upload, pass that context
    return render_template('create_quiz.html', grades=teacher_grades, resource_id=resource_id)

@app.route('/teacher/quizzes')
@login_required
@teacher_required
def teacher_quizzes():
    # Get active quizzes created by this teacher (standalone quiz resources)
    active_quizzes = db.session.query(Resource, QuizMetadata).join(
        QuizMetadata, Resource.id == QuizMetadata.resource_id
    ).filter(
        Resource.created_by == current_user.id,
        Resource.resource_type == 'quiz',
        Resource.is_deleted == False
    ).order_by(Resource.created_at.desc()).all()
    
    # Get resources that have questions attached (quiz linked to original resource)
    resources_with_quizzes = db.session.query(Resource).filter(
        Resource.created_by == current_user.id,
        Resource.resource_type.in_(['note', 'video', 'link']),
        Resource.is_deleted == False
    ).join(Question, Resource.id == Question.resource_id).distinct().order_by(Resource.created_at.desc()).all()
    
    # Get deleted quizzes created by this teacher
    deleted_quizzes = db.session.query(Resource, QuizMetadata).join(
        QuizMetadata, Resource.id == QuizMetadata.resource_id
    ).filter(
        Resource.created_by == current_user.id,
        Resource.resource_type == 'quiz',
        Resource.is_deleted == True
    ).order_by(Resource.deleted_at.desc()).all()
    
    # Get quiz statistics for active quizzes
    active_quiz_stats = []
    for quiz, metadata in active_quizzes:
        # Count students who have taken this quiz
        student_count = StudySession.query.filter_by(resource_id=quiz.id).count()
        # Get average score
        avg_score = db.session.query(db.func.avg(StudySession.quiz_score)).filter(
            StudySession.resource_id == quiz.id,
            StudySession.quiz_score.isnot(None)
        ).scalar()
        
        active_quiz_stats.append({
            'quiz': quiz,
            'metadata': metadata,
            'student_count': student_count,
            'avg_score': avg_score or 0,
            'quiz_type': 'standalone'
        })
    
    # Get quiz statistics for resources with attached quizzes
    resource_quiz_stats = []
    for resource in resources_with_quizzes:
        # Get quiz metadata if it exists
        metadata = QuizMetadata.query.filter_by(resource_id=resource.id).first()
        
        # Count students who have taken this quiz
        student_count = StudySession.query.filter_by(resource_id=resource.id).count()
        # Get average score
        avg_score = db.session.query(db.func.avg(StudySession.quiz_score)).filter(
            StudySession.resource_id == resource.id,
            StudySession.quiz_score.isnot(None)
        ).scalar()
        
        # Count questions for this resource
        question_count = Question.query.filter_by(resource_id=resource.id).count()
        
        resource_quiz_stats.append({
            'quiz': resource,
            'metadata': metadata,
            'student_count': student_count,
            'avg_score': avg_score or 0,
            'question_count': question_count,
            'quiz_type': 'attached'
        })
    
    # Get quiz statistics for deleted quizzes
    deleted_quiz_stats = []
    for quiz, metadata in deleted_quizzes:
        # Count students who have taken this quiz
        student_count = StudySession.query.filter_by(resource_id=quiz.id).count()
        # Get average score
        avg_score = db.session.query(db.func.avg(StudySession.quiz_score)).filter(
            StudySession.resource_id == quiz.id,
            StudySession.quiz_score.isnot(None)
        ).scalar()
        
        deleted_quiz_stats.append({
            'quiz': quiz,
            'metadata': metadata,
            'student_count': student_count,
            'avg_score': avg_score or 0
        })
    
    return render_template('teacher_quizzes.html', 
                         active_quiz_stats=active_quiz_stats,
                         resource_quiz_stats=resource_quiz_stats,
                         deleted_quiz_stats=deleted_quiz_stats)

@app.route('/teacher/quiz/<int:quiz_id>/results')
@login_required
@teacher_required
def quiz_results(quiz_id):
    try:
        quiz = Resource.query.get_or_404(quiz_id)
        if quiz.created_by != current_user.id:
            abort(403)
        
        # Ensure resource type is quiz
        if quiz.resource_type != 'quiz':
            # Try to fix it automatically if it's not a quiz
            quiz.resource_type = 'quiz'
            db.session.commit()
        
        # Get all student attempts for this quiz
        attempts = db.session.query(StudySession, Student).join(
            Student, StudySession.student_id == Student.id
        ).filter(
            StudySession.resource_id == quiz_id
        ).order_by(StudySession.end_time.desc()).all()
        
        # Get quiz questions for reference
        questions = Question.query.filter_by(resource_id=quiz_id).all()
        
        # Get all student answers for this quiz for question analysis
        question_answers = {}
        total_marks = sum(q.marks for q in questions) if questions else 0
        
        for question in questions:
            question_answers[question.id] = []
            for attempt, student in attempts:
                if attempt.completed:
                    answer = StudentAnswer.query.filter_by(
                        student_id=student.id, 
                        question_id=question.id
                    ).join(StudySession, StudySession.student_id == StudentAnswer.student_id
                    ).filter(StudySession.id == attempt.id
                    ).first()
                    if answer:
                        question_answers[question.id].append((student, answer))
                    
                    # If quiz_score is None but answers exist, try to calculate it
                    if attempt.quiz_score is None and attempt.completed:
                        _finalize_quiz_session(student, quiz_id)
                        db.session.refresh(attempt)  # Refresh to get updated score
        
        # Get quiz metadata - create if it doesn't exist
        metadata = QuizMetadata.query.filter_by(resource_id=quiz_id).first()
        if not metadata:
            metadata = QuizMetadata(
                resource_id=quiz_id,
                time_limit=1800,  # 30 minutes default
                passing_score=70,
                created_by=current_user.id,
                marks_published=False
            )
            db.session.add(metadata)
            db.session.commit()
        
        # Calculate statistics for the quiz
        quiz_stats = {
            'total_marks': total_marks,
            'total_questions': len(questions),
            'total_attempts': len(attempts),
            'completed_attempts': len([a for a, _ in attempts if a.completed]),
            'average_score': 0,
            'highest_score': 0,
            'lowest_score': 0
        }
        
        # Calculate statistics if there are completed attempts
        completed_attempts = [a for a, _ in attempts if a.completed and a.quiz_score is not None]
        if completed_attempts:
            scores = [a.quiz_score for a in completed_attempts]
            quiz_stats['average_score'] = sum(scores) / len(scores)
            quiz_stats['highest_score'] = max(scores)
            quiz_stats['lowest_score'] = min(scores)
        
        # Refresh attempts to get any updated scores
        attempts = db.session.query(StudySession, Student).join(
            Student, StudySession.student_id == Student.id
        ).filter(
            StudySession.resource_id == quiz_id
        ).order_by(StudySession.end_time.desc()).all()
        
        return render_template('quiz_results.html', 
                             quiz=quiz, 
                             attempts=attempts, 
                             questions=questions,
                             question_answers=question_answers,
                             metadata=metadata,
                             quiz_stats=quiz_stats,
                             total_marks=total_marks)
    except Exception as e:
        import traceback
        print(f"Error in quiz_results: {str(e)}\n{traceback.format_exc()}")
        flash(f'Error loading quiz results: {str(e)}', 'danger')
        return redirect(url_for('teacher_quizzes'))

@app.route('/teacher/quiz/<int:quiz_id>/edit', methods=['GET', 'POST'])
@login_required
@teacher_required
def edit_quiz(quiz_id):
    quiz = Resource.query.get_or_404(quiz_id)
    if quiz.created_by != current_user.id:
        abort(403)
    
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        time_limit = request.form.get('time_limit')
        passing_score = request.form.get('passing_score')
        questions_data = request.form.get('questions_data')
        
        if not all([title, questions_data]):
            flash('Required fields are missing!', 'danger')
            return redirect(url_for('edit_quiz', quiz_id=quiz_id))
        
        try:
            # Update quiz resource
            quiz.title = title
            quiz.description = description or f"Quiz: {title}"
            
            # Update metadata
            metadata = QuizMetadata.query.filter_by(resource_id=quiz_id).first()
            if metadata:
                # Convert time_limit from minutes to seconds
                time_limit_seconds = None
                if time_limit and time_limit.strip():
                    try:
                        time_limit_seconds = int(time_limit) * 60  # Convert minutes to seconds
                    except ValueError:
                        time_limit_seconds = None
                
                metadata.time_limit = time_limit_seconds
                metadata.passing_score = int(passing_score) if passing_score else 70
            
            # Delete existing questions and recreate
            Question.query.filter_by(resource_id=quiz_id).delete()
            
            # Add new questions
            questions = json.loads(questions_data)
            for q in questions:
                q_type = q.get('question_type', 'mcq')
                options = q.get('options') if q_type == 'mcq' else None
                correct_answer = q.get('correct_answer') if q_type == 'mcq' else None
                question = Question(
                    resource_id=quiz_id,
                    question_text=q['question'],
                    correct_answer=correct_answer,
                    options=options,
                    question_type=q_type
                )
                db.session.add(question)
            
            db.session.commit()
            flash('Quiz updated successfully!', 'success')
            return redirect(url_for('teacher_quizzes'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating quiz: {str(e)}', 'danger')
            return redirect(url_for('edit_quiz', quiz_id=quiz_id))
    
    # Get current quiz data
    metadata = QuizMetadata.query.filter_by(resource_id=quiz_id).first()
    questions = Question.query.filter_by(resource_id=quiz_id).all()
    
    # Get grades this teacher teaches
    teacher_students = Student.query.filter_by(teacher_id=current_user.id).all()
    teacher_grades = sorted(list(set([s.grade for s in teacher_students])))
    
    return render_template('edit_quiz.html', 
                         quiz=quiz, 
                         metadata=metadata, 
                         questions=questions,
                         grades=teacher_grades)

@app.route('/teacher/quiz/<int:quiz_id>/delete', methods=['POST'])
@login_required
@teacher_required
def delete_quiz(quiz_id):
    quiz = Resource.query.get_or_404(quiz_id)
    if quiz.created_by != current_user.id:
        abort(403)
    
    try:
        # Soft delete the quiz resource (keep all student data intact)
        quiz.is_deleted = True
        quiz.deleted_at = datetime.now()
        quiz.deleted_by = current_user.id
        
        # Keep all related data (questions, metadata, study sessions, student answers)
        # This ensures students don't lose their quiz records
        
        db.session.commit()
        
        flash('Quiz deleted successfully! Student records have been preserved.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting quiz: {str(e)}', 'danger')
    
    return redirect(url_for('teacher_quizzes'))

@app.route('/teacher/quiz/<int:quiz_id>/restore', methods=['POST'])
@login_required
@teacher_required
def restore_quiz(quiz_id):
    quiz = Resource.query.get_or_404(quiz_id)
    if quiz.created_by != current_user.id:
        abort(403)
    
    try:
        # Restore the quiz resource
        quiz.is_deleted = False
        quiz.deleted_at = None
        quiz.deleted_by = None
        
        db.session.commit()
        
        flash('Quiz restored successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error restoring quiz: {str(e)}', 'danger')
    
    return redirect(url_for('teacher_quizzes'))

@app.route('/debug/resource/<int:resource_id>')
@login_required
@teacher_required
def debug_resource(resource_id):
    """Debug endpoint to check resource details"""
    resource = Resource.query.get_or_404(resource_id)
    return jsonify({
        'id': resource.id,
        'title': resource.title,
        'resource_type': resource.resource_type,
        'created_by': resource.created_by,
        'is_deleted': resource.is_deleted if hasattr(resource, 'is_deleted') else None,
        'questions_count': Question.query.filter_by(resource_id=resource_id).count(),
        'metadata': {
            'exists': bool(QuizMetadata.query.filter_by(resource_id=resource_id).first())
        } if resource.resource_type == 'quiz' else None
    })

@app.route('/fix_quiz_resource/<int:resource_id>')
@login_required
@teacher_required
def fix_quiz_resource(resource_id):
    """Fix the resource type for an existing quiz"""
    try:
        resource = Resource.query.get_or_404(resource_id)
        if resource.created_by != current_user.id:
            abort(403)
            
        # Check if there are any questions for this resource
        question_count = Question.query.filter_by(resource_id=resource_id).count()
        if question_count == 0:
            flash('No questions found for this resource. Cannot convert to quiz.', 'warning')
            return redirect(url_for('teacher_quizzes'))
            
        # Update the resource type
        resource.resource_type = 'quiz'
        
        # Ensure quiz metadata exists
        metadata = QuizMetadata.query.filter_by(resource_id=resource_id).first()
        if not metadata:
            metadata = QuizMetadata(
                resource_id=resource_id,
                time_limit=1800,  # 30 minutes default
                passing_score=70,
                created_by=current_user.id
            )
            db.session.add(metadata)
        
        db.session.commit()
        flash(f'Successfully converted resource to quiz with {question_count} questions!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error fixing quiz resource: {str(e)}', 'danger')
        
    return redirect(url_for('teacher_quizzes'))

if __name__ == '__main__':
    with app.app_context():
        try:
            db.create_all()
        except Exception:
            pass
    # Start nightly background trainer
    # start_nightly_trainer()  # Temporarily disabled to fix startup issue
    # Production-ready: Use environment variables for port and debug mode
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug) 