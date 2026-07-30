"""SQLAlchemy models for DROP.

Covers users, classrooms, the AI-generated curriculum (weeks/lessons),
assessments, submissions, messaging, notifications, AI chat history,
solo study sessions, gamification, and adaptive learning profiles.
"""
import json
from datetime import datetime, timezone

from flask_login import UserMixin

from extensions import db


def now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'teacher' | 'student'
    avatar_seed = db.Column(db.String(40), default="drop")
    theme = db.Column(db.String(10), default="light")  # 'light' | 'dark'

    # Gamification (students)
    xp = db.Column(db.Integer, default=0)
    level = db.Column(db.Integer, default=1)
    coins = db.Column(db.Integer, default=0)
    streak_days = db.Column(db.Integer, default=0)
    last_active = db.Column(db.DateTime, default=now)

    created_at = db.Column(db.DateTime, default=now)

    classrooms = db.relationship(
        "Classroom", backref="teacher", lazy=True, foreign_keys="Classroom.teacher_id"
    )
    enrollments = db.relationship("Enrollment", backref="student", lazy=True)

    def xp_to_next_level(self):
        return self.level * 500

    def add_xp(self, amount):
        self.xp += amount
        while self.xp >= self.xp_to_next_level():
            self.xp -= self.xp_to_next_level()
            self.level += 1
        db.session.commit()


# ---------------------------------------------------------------------------
# Classrooms
# ---------------------------------------------------------------------------
class Classroom(db.Model):
    __tablename__ = "classrooms"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    subject = db.Column(db.String(120), nullable=False)
    duration_weeks = db.Column(db.Integer, default=8)
    target_grade = db.Column(db.String(60))
    syllabus_text = db.Column(db.Text)
    join_code = db.Column(db.String(10), unique=True, nullable=False)

    ai_course_json = db.Column(db.Text)  # raw AI course plan (exams etc.)
    ai_status = db.Column(db.String(20), default="pending")  # pending|ready|failed

    created_at = db.Column(db.DateTime, default=now)

    weeks = db.relationship(
        "Week", backref="classroom", lazy=True, cascade="all, delete-orphan",
        order_by="Week.number",
    )
    enrollments = db.relationship(
        "Enrollment", backref="classroom", lazy=True, cascade="all, delete-orphan"
    )
    assignments = db.relationship(
        "Assignment", backref="classroom", lazy=True, cascade="all, delete-orphan"
    )
    messages = db.relationship(
        "Message", backref="classroom", lazy=True, cascade="all, delete-orphan"
    )

    def course(self):
        try:
            return json.loads(self.ai_course_json) if self.ai_course_json else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def student_count(self):
        return len(self.enrollments)


class Enrollment(db.Model):
    __tablename__ = "enrollments"

    id = db.Column(db.Integer, primary_key=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classrooms.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    joined_at = db.Column(db.DateTime, default=now)
    progress_percent = db.Column(db.Float, default=0.0)
    predicted_grade = db.Column(db.String(20))
    risk_level = db.Column(db.String(20), default="low")  # low|medium|high


class Week(db.Model):
    __tablename__ = "weeks"

    id = db.Column(db.Integer, primary_key=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classrooms.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200))
    summary = db.Column(db.Text)

    lessons = db.relationship(
        "Lesson", backref="week", lazy=True, cascade="all, delete-orphan",
        order_by="Lesson.order",
    )


class Lesson(db.Model):
    __tablename__ = "lessons"

    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.Integer, db.ForeignKey("weeks.id"), nullable=False)
    order = db.Column(db.Integer, default=0)
    title = db.Column(db.String(200), nullable=False)

    objectives = db.Column(db.Text)          # JSON list
    notes = db.Column(db.Text)                # lecture notes (markdown)
    definitions = db.Column(db.Text)          # JSON list
    examples = db.Column(db.Text)             # markdown
    applications = db.Column(db.Text)         # markdown
    common_mistakes = db.Column(db.Text)      # JSON list
    practice = db.Column(db.Text)             # JSON list of practice Qs
    revision = db.Column(db.Text)             # markdown
    summary = db.Column(db.Text)              # markdown
    homework = db.Column(db.Text)             # JSON list
    quiz_json = db.Column(db.Text)            # JSON mini-quiz

    def get_json(self, field):
        raw = getattr(self, field)
        try:
            return json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            return []


class LessonProgress(db.Model):
    """Tracks a student's progress through a specific lesson."""
    __tablename__ = "lesson_progress"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lessons.id"), nullable=False)
    completed = db.Column(db.Boolean, default=False)
    quiz_score = db.Column(db.Float)
    completed_at = db.Column(db.DateTime)

    lesson = db.relationship("Lesson")


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------
class Assignment(db.Model):
    __tablename__ = "assignments"

    id = db.Column(db.Integer, primary_key=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classrooms.id"), nullable=False)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lessons.id"), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    kind = db.Column(db.String(20), default="assignment")
    # kind in: classwork | assignment | weekly_test | monthly_test | midterm | final_exam
    questions_json = db.Column(db.Text)  # JSON list of question dicts
    due_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now)

    submissions = db.relationship(
        "Submission", backref="assignment", lazy=True, cascade="all, delete-orphan"
    )

    def questions(self):
        try:
            return json.loads(self.questions_json) if self.questions_json else []
        except (json.JSONDecodeError, TypeError):
            return []


class Submission(db.Model):
    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    answers_json = db.Column(db.Text)
    score = db.Column(db.Float)
    max_score = db.Column(db.Float, default=100)
    feedback = db.Column(db.Text)             # JSON list of per-question feedback
    misconceptions = db.Column(db.Text)       # JSON list e.g. "concept misunderstanding"
    status = db.Column(db.String(20), default="submitted")  # submitted|graded
    submitted_at = db.Column(db.DateTime, default=now)
    graded_at = db.Column(db.DateTime)

    student = db.relationship("User", foreign_keys=[student_id])

    def answers(self):
        try:
            return json.loads(self.answers_json) if self.answers_json else []
        except (json.JSONDecodeError, TypeError):
            return []

    def feedback_list(self):
        try:
            return json.loads(self.feedback) if self.feedback else []
        except (json.JSONDecodeError, TypeError):
            return []


# ---------------------------------------------------------------------------
# Messaging & notifications
# ---------------------------------------------------------------------------
class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classrooms.id"), nullable=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    content = db.Column(db.Text, nullable=False)
    is_announcement = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now)

    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.String(300), nullable=False)
    kind = db.Column(db.String(30), default="info")
    # kind: inactive|missing_assignment|performance_drop|performance_up|exam_risk|info
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now)


# ---------------------------------------------------------------------------
# AI Tutor / Solo Study
# ---------------------------------------------------------------------------
class AIChatMessage(db.Model):
    __tablename__ = "ai_chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classrooms.id"), nullable=True)
    role = db.Column(db.String(10), nullable=False)  # 'user' | 'assistant'
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=now)


class StudySession(db.Model):
    """Solo study: student uploads a topic/PDF and AI builds a mini-course."""
    __tablename__ = "study_sessions"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    topic = db.Column(db.String(200))
    source_filename = db.Column(db.String(300))
    source_text = db.Column(db.Text)
    ai_course_json = db.Column(db.Text)
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=now)

    def course(self):
        try:
            return json.loads(self.ai_course_json) if self.ai_course_json else {}
        except (json.JSONDecodeError, TypeError):
            return {}


# ---------------------------------------------------------------------------
# Gamification & adaptive learning
# ---------------------------------------------------------------------------
class Achievement(db.Model):
    __tablename__ = "achievements"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(300))
    icon = db.Column(db.String(50), default="award")
    earned_at = db.Column(db.DateTime, default=now)


class LearningProfile(db.Model):
    __tablename__ = "learning_profiles"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    weak_topics_json = db.Column(db.Text, default="[]")
    strong_topics_json = db.Column(db.Text, default="[]")
    learning_speed = db.Column(db.String(20), default="average")  # slow|average|fast
    attention_span = db.Column(db.String(20), default="medium")
    preferred_difficulty = db.Column(db.String(20), default="medium")
    confidence = db.Column(db.Integer, default=50)  # 0-100
    updated_at = db.Column(db.DateTime, default=now)

    def weak_topics(self):
        try:
            return json.loads(self.weak_topics_json) if self.weak_topics_json else []
        except (json.JSONDecodeError, TypeError):
            return []

    def strong_topics(self):
        try:
            return json.loads(self.strong_topics_json) if self.strong_topics_json else []
        except (json.JSONDecodeError, TypeError):
            return []