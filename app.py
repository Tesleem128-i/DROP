"""DROP — The AI Teacher That Never Stops Teaching.

ALL routes live in this single file per project spec. Helper logic lives in
models.py (database), extensions.py (Flask extensions), ai_engine.py (Grok
integration), and config.py (settings).
"""
import json
import os
import random
import string
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from flask import (
    Flask, render_template, redirect, url_for, request, flash, jsonify, abort, session,
    current_app
)
from flask_login import (
    login_user, logout_user, login_required, current_user
)
from werkzeug.utils import secure_filename

from config import Config
from extensions import db, login_manager, bcrypt
import models as m
import ai_engine as ai

# ---------------------------------------------------------------------------
# App factory / bootstrap
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
login_manager.init_app(app)
bcrypt.init_app(app)

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Ensure the directory for the SQLite file actually exists, derived from the
# resolved URI itself (not just a hardcoded guess), so this works even if
# DATABASE_URL was overridden via .env to point somewhere else.
_db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
if _db_uri.startswith("sqlite:///"):
    _db_file_path = _db_uri[len("sqlite:///"):]
    _db_dir = os.path.dirname(_db_file_path)
    if _db_dir:
        os.makedirs(_db_dir, exist_ok=True)

print(f"[DROP] Using database: {_db_uri}")

with app.app_context():
    db.create_all()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(m.User, int(user_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def gen_join_code(length=6):
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=length))
        if not m.Classroom.query.filter_by(join_code=code).first():
            return code


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]


def extract_text_from_upload(file_storage):
    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[-1].lower()
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file_storage.save(path)
    text = ""
    try:
        if ext == "pdf":
            from PyPDF2 import PdfReader
            reader = PdfReader(path)
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        elif ext == "docx":
            import docx
            doc = docx.Document(path)
            text = "\n".join(p.text for p in doc.paragraphs)
        else:
            with open(path, "r", errors="ignore") as f:
                text = f.read()
    except Exception:
        text = ""
    return filename, text


def require_role(role):
    if not current_user.is_authenticated or current_user.role != role:
        abort(403)


def teacher_owns_classroom(classroom):
    if classroom.teacher_id != current_user.id:
        abort(403)


def student_enrolled(classroom):
    enrollment = m.Enrollment.query.filter_by(
        classroom_id=classroom.id, student_id=current_user.id
    ).first()
    if not enrollment:
        abort(403)
    return enrollment


def push_notification(user_id, content, kind="info"):
    n = m.Notification(user_id=user_id, content=content, kind=kind)
    db.session.add(n)
    db.session.commit()


def unread_notification_count():
    if not current_user.is_authenticated:
        return 0
    return m.Notification.query.filter_by(user_id=current_user.id, read=False).count()


@app.context_processor
def inject_globals():
    return {
        "unread_notifications": unread_notification_count() if current_user.is_authenticated else 0,
        "now": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if current_user.is_authenticated:
        if current_user.role == "teacher":
            return redirect(url_for("teacher_dashboard"))
        return redirect(url_for("student_dashboard"))
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "student")

        if not name or not email or not password:
            flash("Please fill in every field.", "error")
            return render_template("auth/signup.html")

        if role not in ("teacher", "student"):
            role = "student"

        if m.User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return render_template("auth/signup.html")

        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        user = m.User(name=name, email=email, password_hash=pw_hash, role=role)
        db.session.add(user)
        db.session.commit()

        if role == "student":
            db.session.add(m.LearningProfile(student_id=user.id))
            db.session.commit()

        login_user(user)
        flash(f"Welcome to DROP, {name.split(' ')[0]}!", "success")
        return redirect(url_for("index"))

    return render_template("auth/signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = m.User.query.filter_by(email=email).first()

        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user, remember=True)
            user.last_active = datetime.now(timezone.utc)
            db.session.commit()
            return redirect(url_for("index"))

        flash("Incorrect email or password.", "error")

    return render_template("auth/login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        flash("If that email exists in DROP, a reset link has been sent.", "success")
        return redirect(url_for("login"))
    return render_template("auth/forgot_password.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Notifications (shared)
# ---------------------------------------------------------------------------
@app.route("/notifications")
@login_required
def notifications():
    items = m.Notification.query.filter_by(user_id=current_user.id).order_by(
        m.Notification.created_at.desc()
    ).all()
    for n in items:
        n.read = True
    db.session.commit()
    return render_template("shared/notifications.html", items=items)


# ---------------------------------------------------------------------------
# TEACHER — Dashboard
# ---------------------------------------------------------------------------
@app.route("/teacher/dashboard")
@login_required
def teacher_dashboard():
    require_role("teacher")
    classrooms = m.Classroom.query.filter_by(teacher_id=current_user.id).order_by(
        m.Classroom.created_at.desc()
    ).all()

    total_students = sum(c.student_count() for c in classrooms)
    pending_grading = (
        m.Submission.query.join(m.Assignment).join(m.Classroom)
        .filter(m.Classroom.teacher_id == current_user.id, m.Submission.status == "submitted")
        .count()
    )

    return render_template(
        "teacher/dashboard.html",
        classrooms=classrooms,
        total_students=total_students,
        pending_grading=pending_grading,
    )


@app.route("/teacher/classroom/create", methods=["GET", "POST"])
@login_required
def teacher_classroom_create():
    require_role("teacher")

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        subject = request.form.get("subject", "").strip()
        duration_weeks = int(request.form.get("duration_weeks") or 8)
        target_grade = request.form.get("target_grade", "").strip()
        syllabus_text = ""

        syllabus_file = request.files.get("syllabus")
        if syllabus_file and syllabus_file.filename and allowed_file(syllabus_file.filename):
            _, syllabus_text = extract_text_from_upload(syllabus_file)

        classroom = m.Classroom(
            teacher_id=current_user.id,
            name=name,
            subject=subject,
            duration_weeks=duration_weeks,
            target_grade=target_grade,
            syllabus_text=syllabus_text,
            join_code=gen_join_code(),
            ai_status="pending",
        )
        db.session.add(classroom)
        db.session.commit()

        # --- AI generates the entire course right now (synchronous for simplicity) ---
        try:
            course = ai.generate_course(subject, duration_weeks, target_grade, syllabus_text)
            _persist_course(classroom, course)
            classroom.ai_status = "ready"
        except Exception as e:
            current_app.logger.exception("Classroom AI course generation failed")
            classroom.ai_status = "failed"
            flash(f"AI course generation had an issue, a starter course was created instead.", "error")
        db.session.commit()

        flash("Classroom created — Grok generated your full course.", "success")
        return redirect(url_for("teacher_classroom_overview", classroom_id=classroom.id))

    return render_template("teacher/classroom_create.html")


def _persist_course(classroom, course):
    """Persist an AI-generated course dict into Week/Lesson rows + raw JSON."""
    classroom.ai_course_json = json.dumps(course)

    for week_data in course.get("weeks", []):
        week = m.Week(
            classroom_id=classroom.id,
            number=week_data.get("number", 1),
            title=week_data.get("title", f"Week {week_data.get('number', 1)}"),
            summary=week_data.get("summary", ""),
        )
        db.session.add(week)
        db.session.flush()

        for idx, lesson_data in enumerate(week_data.get("lessons", [])):
            lesson = m.Lesson(
                week_id=week.id,
                order=idx,
                title=lesson_data.get("title", "Untitled lesson"),
                objectives=json.dumps(lesson_data.get("objectives", [])),
                notes=lesson_data.get("notes", ""),
                definitions=json.dumps(lesson_data.get("definitions", [])),
                examples=lesson_data.get("examples", ""),
                applications=lesson_data.get("applications", ""),
                common_mistakes=json.dumps(lesson_data.get("common_mistakes", [])),
                practice=json.dumps(lesson_data.get("practice", [])),
                revision=lesson_data.get("revision", ""),
                summary=lesson_data.get("summary", ""),
                homework=json.dumps(lesson_data.get("homework", [])),
                quiz_json=json.dumps(lesson_data.get("quiz", [])),
            )
            db.session.add(lesson)

        # Weekly test as an Assignment of kind weekly_test
        weekly_test = week_data.get("weekly_test")
        if weekly_test:
            db.session.add(m.Assignment(
                classroom_id=classroom.id,
                title=f"Week {week.number} Test",
                description=f"Auto-generated weekly test for {week.title}",
                kind="weekly_test",
                questions_json=json.dumps(weekly_test.get("questions", [])),
                due_date=datetime.now(timezone.utc) + timedelta(weeks=week.number),
            ))

    if course.get("midterm"):
        db.session.add(m.Assignment(
            classroom_id=classroom.id, title="Midterm Exam",
            description="AI-generated midterm exam.", kind="midterm",
            questions_json=json.dumps(course["midterm"].get("questions", [])),
            due_date=datetime.now(timezone.utc) + timedelta(weeks=max(classroom.duration_weeks // 2, 1)),
        ))

    if course.get("final_exam"):
        db.session.add(m.Assignment(
            classroom_id=classroom.id, title="Final Exam",
            description="AI-generated final exam.", kind="final_exam",
            questions_json=json.dumps(course["final_exam"].get("questions", [])),
            due_date=datetime.now(timezone.utc) + timedelta(weeks=classroom.duration_weeks),
        ))

    db.session.commit()


@app.route("/teacher/classroom/<int:classroom_id>")
@login_required
def teacher_classroom_overview(classroom_id):
    require_role("teacher")
    classroom = db.get_or_404(m.Classroom, classroom_id)
    teacher_owns_classroom(classroom)
    return render_template("teacher/classroom_overview.html", classroom=classroom)


@app.route("/teacher/classroom/<int:classroom_id>/students")
@login_required
def teacher_classroom_students(classroom_id):
    require_role("teacher")
    classroom = db.get_or_404(m.Classroom, classroom_id)
    teacher_owns_classroom(classroom)
    enrollments = classroom.enrollments
    return render_template("teacher/classroom_students.html", classroom=classroom, enrollments=enrollments)


@app.route("/teacher/classroom/<int:classroom_id>/student/<int:student_id>")
@login_required
def teacher_student_detail(classroom_id, student_id):
    require_role("teacher")
    classroom = db.get_or_404(m.Classroom, classroom_id)
    teacher_owns_classroom(classroom)
    student = db.get_or_404(m.User, student_id)
    enrollment = m.Enrollment.query.filter_by(classroom_id=classroom.id, student_id=student.id).first_or_404()

    submissions = (
        m.Submission.query.join(m.Assignment)
        .filter(m.Assignment.classroom_id == classroom.id, m.Submission.student_id == student.id)
        .order_by(m.Submission.submitted_at.desc()).all()
    )
    profile = m.LearningProfile.query.filter_by(student_id=student.id).first()
    graded = [s for s in submissions if s.score is not None]
    avg_score = round(sum(s.score for s in graded) / len(graded), 1) if graded else None

    return render_template(
        "teacher/student_detail.html", classroom=classroom, student=student,
        enrollment=enrollment, submissions=submissions, profile=profile, avg_score=avg_score,
    )


@app.route("/teacher/classroom/<int:classroom_id>/analytics")
@login_required
def teacher_classroom_analytics(classroom_id):
    require_role("teacher")
    classroom = db.get_or_404(m.Classroom, classroom_id)
    teacher_owns_classroom(classroom)

    assignments = classroom.assignments
    all_scores = []
    for a in assignments:
        for s in a.submissions:
            if s.score is not None:
                all_scores.append(s.score)

    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
    completion_rate = 0
    total_possible = len(assignments) * max(classroom.student_count(), 1)
    if total_possible:
        completion_rate = round(
            sum(len(a.submissions) for a in assignments) / total_possible * 100, 1
        )

    ranking = sorted(
        classroom.enrollments,
        key=lambda e: e.progress_percent or 0,
        reverse=True,
    )

    stats_summary = {
        "average_score": avg_score,
        "completion_rate": completion_rate,
        "students": classroom.student_count(),
        "assignments": len(assignments),
    }
    try:
        insights = ai.generate_class_insights(stats_summary).get("insights", [])
    except Exception:
        insights = ["AI insights are temporarily unavailable."]

    return render_template(
        "teacher/analytics.html", classroom=classroom, avg_score=avg_score,
        completion_rate=completion_rate, ranking=ranking, insights=insights,
    )


@app.route("/teacher/classroom/<int:classroom_id>/settings", methods=["GET", "POST"])
@login_required
def teacher_classroom_settings(classroom_id):
    require_role("teacher")
    classroom = db.get_or_404(m.Classroom, classroom_id)
    teacher_owns_classroom(classroom)

    if request.method == "POST":
        classroom.name = request.form.get("name", classroom.name)
        classroom.subject = request.form.get("subject", classroom.subject)
        classroom.target_grade = request.form.get("target_grade", classroom.target_grade)
        db.session.commit()
        flash("Classroom settings updated.", "success")
        return redirect(url_for("teacher_classroom_settings", classroom_id=classroom.id))

    return render_template("teacher/classroom_settings.html", classroom=classroom)


@app.route("/teacher/classroom/<int:classroom_id>/messages", methods=["GET", "POST"])
@login_required
def teacher_classroom_messages(classroom_id):
    require_role("teacher")
    classroom = db.get_or_404(m.Classroom, classroom_id)
    teacher_owns_classroom(classroom)

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if content:
            msg = m.Message(
                classroom_id=classroom.id, sender_id=current_user.id,
                content=content, is_announcement=True,
            )
            db.session.add(msg)
            db.session.commit()
            for e in classroom.enrollments:
                push_notification(e.student_id, f"New announcement in {classroom.name}", "info")
        return redirect(url_for("teacher_classroom_messages", classroom_id=classroom.id))

    messages = m.Message.query.filter_by(classroom_id=classroom.id).order_by(
        m.Message.created_at.desc()
    ).all()
    return render_template("teacher/classroom_messages.html", classroom=classroom, messages=messages)


@app.route("/teacher/assignment/<int:assignment_id>/submissions")
@login_required
def teacher_assignment_submissions(assignment_id):
    require_role("teacher")
    assignment = db.get_or_404(m.Assignment, assignment_id)
    classroom = assignment.classroom
    teacher_owns_classroom(classroom)
    submissions = m.Submission.query.filter_by(assignment_id=assignment.id).order_by(
        m.Submission.submitted_at.desc()
    ).all()
    return render_template(
        "teacher/assignment_submissions.html", assignment=assignment,
        classroom=classroom, submissions=submissions,
    )


@app.route("/teacher/classroom/<int:classroom_id>/assignment/create", methods=["GET", "POST"])
@login_required
def teacher_assignment_create(classroom_id):
    require_role("teacher")
    classroom = db.get_or_404(m.Classroom, classroom_id)
    teacher_owns_classroom(classroom)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        kind = request.form.get("kind", "assignment")
        description = request.form.get("description", "")
        try:
            # Ask Grok to generate questions for this specific assignment,
            # grounded in whatever the teacher uploaded for this classroom.
            questions = ai.generate_assignment_questions(
                classroom.subject, title, description, kind,
                source_text=classroom.syllabus_text or "",
            )
        except Exception:
            current_app.logger.exception("Assignment question generation failed")
            questions = [{"question": f"Question about {title}", "answer": "Model answer",
                          "type": "short_answer", "options": []}]

        assignment = m.Assignment(
            classroom_id=classroom.id, title=title, description=description,
            kind=kind, questions_json=json.dumps(questions),
            due_date=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.session.add(assignment)
        db.session.commit()
        for e in classroom.enrollments:
            push_notification(e.student_id, f"New {kind.replace('_',' ')}: {title}", "info")
        flash("Assignment created with AI-generated questions.", "success")
        return redirect(url_for("teacher_classroom_overview", classroom_id=classroom.id))

    return render_template("teacher/assignment_create.html", classroom=classroom)


@app.route("/teacher/submission/<int:submission_id>/grade", methods=["GET", "POST"])
@login_required
def teacher_grade_submission(submission_id):
    require_role("teacher")
    submission = db.get_or_404(m.Submission, submission_id)
    assignment = submission.assignment
    teacher_owns_classroom(assignment.classroom)

    if request.method == "POST":
        overall_score = float(request.form.get("score", submission.score or 0))
        feedback_note = request.form.get("feedback", "")
        submission.score = overall_score
        submission.feedback = json.dumps([{"note": feedback_note}])
        submission.status = "graded"
        submission.graded_at = datetime.now(timezone.utc)
        db.session.commit()
        push_notification(submission.student_id, f"Your submission for {assignment.title} was graded.", "info")
        flash("Submission graded.", "success")
        return redirect(url_for("teacher_assignment_submissions", assignment_id=assignment.id))

    return render_template("teacher/grade_submission.html", submission=submission, assignment=assignment)


@app.route("/teacher/submission/<int:submission_id>/auto-grade", methods=["POST"])
@login_required
def teacher_auto_grade_submission(submission_id):
    """Trigger Grok's understanding-engine grading for every answer in a submission."""
    require_role("teacher")
    submission = db.get_or_404(m.Submission, submission_id)
    assignment = submission.assignment
    teacher_owns_classroom(assignment.classroom)

    questions = assignment.questions()
    answers = submission.answers()
    feedback_list, misconceptions, total = [], [], 0

    for i, q in enumerate(questions):
        student_answer = answers[i] if i < len(answers) else ""
        result = ai.grade_answer(
            q.get("question", ""), q.get("answer", ""), student_answer,
            q.get("type", "short_answer"),
        )
        feedback_list.append(result)
        misconceptions.append(result.get("misconception", "none"))
        total += result.get("score", 0)

    submission.score = round(total / max(len(questions), 1), 1)
    submission.feedback = json.dumps(feedback_list)
    submission.misconceptions = json.dumps(misconceptions)
    submission.status = "graded"
    submission.graded_at = datetime.now(timezone.utc)
    db.session.commit()
    push_notification(submission.student_id, f"Grok graded your submission for {assignment.title}.", "info")
    flash("Auto-graded with Grok's understanding engine.", "success")
    return redirect(url_for("teacher_assignment_submissions", assignment_id=assignment.id))


# ---------------------------------------------------------------------------
# STUDENT — Dashboard
# ---------------------------------------------------------------------------
@app.route("/student/dashboard")
@login_required
def student_dashboard():
    require_role("student")
    enrollments = m.Enrollment.query.filter_by(student_id=current_user.id).all()
    classrooms = [e.classroom for e in enrollments]

    upcoming = (
        m.Assignment.query.filter(
            m.Assignment.classroom_id.in_([c.id for c in classrooms]) if classrooms else False
        ).order_by(m.Assignment.due_date.asc()).limit(5).all()
        if classrooms else []
    )
    achievements = m.Achievement.query.filter_by(student_id=current_user.id).order_by(
        m.Achievement.earned_at.desc()
    ).limit(4).all()

    return render_template(
        "student/dashboard.html", classrooms=classrooms, enrollments=enrollments,
        upcoming=upcoming, achievements=achievements,
    )


@app.route("/student/join-classroom", methods=["POST"])
@login_required
def student_join_classroom():
    require_role("student")
    code = request.form.get("join_code", "").strip().upper()
    classroom = m.Classroom.query.filter_by(join_code=code).first()
    if not classroom:
        flash("Invalid join code.", "error")
        return redirect(url_for("student_dashboard"))

    existing = m.Enrollment.query.filter_by(classroom_id=classroom.id, student_id=current_user.id).first()
    if existing:
        flash("You're already enrolled in that classroom.", "info")
        return redirect(url_for("student_dashboard"))

    db.session.add(m.Enrollment(classroom_id=classroom.id, student_id=current_user.id))
    db.session.commit()
    push_notification(classroom.teacher_id, f"{current_user.name} joined {classroom.name}.", "info")
    flash(f"Joined {classroom.name}!", "success")
    return redirect(url_for("student_classroom", classroom_id=classroom.id))


@app.route("/student/classroom/<int:classroom_id>")
@login_required
def student_classroom(classroom_id):
    require_role("student")
    classroom = db.get_or_404(m.Classroom, classroom_id)
    enrollment = student_enrolled(classroom)
    return render_template("student/classroom.html", classroom=classroom, enrollment=enrollment)


@app.route("/student/classroom/<int:classroom_id>/lesson/<int:lesson_id>")
@login_required
def student_lesson(classroom_id, lesson_id):
    require_role("student")
    classroom = db.get_or_404(m.Classroom, classroom_id)
    student_enrolled(classroom)
    lesson = db.get_or_404(m.Lesson, lesson_id)

    progress = m.LessonProgress.query.filter_by(
        student_id=current_user.id, lesson_id=lesson.id
    ).first()
    if not progress:
        progress = m.LessonProgress(student_id=current_user.id, lesson_id=lesson.id)
        db.session.add(progress)
        db.session.commit()

    return render_template(
        "student/lesson.html", classroom=classroom, lesson=lesson, progress=progress
    )


@app.route("/student/classroom/<int:classroom_id>/lesson/<int:lesson_id>/quiz", methods=["POST"])
@login_required
def student_lesson_quiz_submit(classroom_id, lesson_id):
    require_role("student")
    classroom = db.get_or_404(m.Classroom, classroom_id)
    enrollment = student_enrolled(classroom)
    lesson = db.get_or_404(m.Lesson, lesson_id)
    quiz = lesson.get_json("quiz_json")

    correct = 0
    for i, q in enumerate(quiz):
        submitted = request.form.get(f"q{i}")
        if submitted and submitted == q.get("answer"):
            correct += 1
    score = round((correct / max(len(quiz), 1)) * 100, 1)

    progress = m.LessonProgress.query.filter_by(student_id=current_user.id, lesson_id=lesson.id).first()
    progress.completed = True
    progress.quiz_score = score
    progress.completed_at = datetime.now(timezone.utc)

    # bump enrollment progress
    total_lessons = sum(len(w.lessons) for w in classroom.weeks) or 1
    done_lessons = (
        m.LessonProgress.query.join(m.Lesson).join(m.Week)
        .filter(m.Week.classroom_id == classroom.id, m.LessonProgress.student_id == current_user.id,
                m.LessonProgress.completed == True).count()  # noqa: E712
    )
    enrollment.progress_percent = round(done_lessons / total_lessons * 100, 1)

    current_user.add_xp(50 if score >= 70 else 20)
    if score == 100 and not m.Achievement.query.filter_by(
        student_id=current_user.id, title="Perfect Quiz"
    ).first():
        db.session.add(m.Achievement(
            student_id=current_user.id, title="Perfect Quiz",
            description="Scored 100% on a lesson quiz.", icon="star",
        ))

    db.session.commit()
    flash(f"Quiz complete — you scored {score}%. +XP earned!", "success")
    return redirect(url_for("student_lesson", classroom_id=classroom.id, lesson_id=lesson.id))


@app.route("/student/study-alone", methods=["GET", "POST"])
@login_required
def student_study_alone():
    require_role("student")

    if request.method == "POST":
        topic = request.form.get("topic", "").strip()
        source_text = ""
        filename = None

        upload = request.files.get("document")
        if upload and upload.filename and allowed_file(upload.filename):
            filename, source_text = extract_text_from_upload(upload)

        if not topic and not source_text:
            flash("Give DROP a topic or upload a document to study.", "error")
            return redirect(url_for("student_study_alone"))

        study = m.StudySession(
            student_id=current_user.id, topic=topic or filename,
            source_filename=filename, source_text=source_text, status="pending",
        )
        db.session.add(study)
        db.session.commit()

        try:
            course = ai.generate_solo_course(topic or filename, source_text)
            study.ai_course_json = json.dumps(course)
            study.status = "ready"
        except Exception:
            current_app.logger.exception("Solo study course generation failed")
            study.status = "failed"
        db.session.commit()

        return redirect(url_for("student_study_session", session_id=study.id))

    sessions = m.StudySession.query.filter_by(student_id=current_user.id).order_by(
        m.StudySession.created_at.desc()
    ).all()
    return render_template("student/study_alone.html", sessions=sessions)


@app.route("/student/study-alone/<int:session_id>")
@login_required
def student_study_session(session_id):
    require_role("student")
    study = db.get_or_404(m.StudySession, session_id)
    if study.student_id != current_user.id:
        abort(403)
    return render_template("student/study_session.html", study=study)


@app.route("/student/assignments")
@login_required
def student_assignments():
    require_role("student")
    classroom_ids = [e.classroom_id for e in current_user.enrollments]
    assignments = (
        m.Assignment.query.filter(
            m.Assignment.classroom_id.in_(classroom_ids), m.Assignment.kind.in_(["assignment", "classwork"])
        ).order_by(m.Assignment.due_date.asc()).all()
        if classroom_ids else []
    )
    my_subs = {s.assignment_id: s for s in m.Submission.query.filter_by(student_id=current_user.id).all()}
    return render_template("student/assignments.html", assignments=assignments, my_subs=my_subs)


@app.route("/student/tests")
@login_required
def student_tests():
    require_role("student")
    classroom_ids = [e.classroom_id for e in current_user.enrollments]
    tests = (
        m.Assignment.query.filter(
            m.Assignment.classroom_id.in_(classroom_ids),
            m.Assignment.kind.in_(["weekly_test", "monthly_test", "midterm", "final_exam"]),
        ).order_by(m.Assignment.due_date.asc()).all()
        if classroom_ids else []
    )
    my_subs = {s.assignment_id: s for s in m.Submission.query.filter_by(student_id=current_user.id).all()}
    return render_template("student/tests.html", tests=tests, my_subs=my_subs)


@app.route("/student/assignment/<int:assignment_id>", methods=["GET", "POST"])
@login_required
def student_assignment_detail(assignment_id):
    require_role("student")
    assignment = db.get_or_404(m.Assignment, assignment_id)
    classroom = assignment.classroom
    student_enrolled(classroom)

    existing = m.Submission.query.filter_by(
        assignment_id=assignment.id, student_id=current_user.id
    ).first()

    if request.method == "POST" and not existing:
        questions = assignment.questions()
        answers = [request.form.get(f"answer_{i}", "") for i in range(len(questions))]
        submission = m.Submission(
            assignment_id=assignment.id, student_id=current_user.id,
            answers_json=json.dumps(answers), status="submitted",
        )
        db.session.add(submission)
        db.session.commit()

        # Auto-grade immediately with Grok's understanding engine
        try:
            feedback_list, misconceptions, total = [], [], 0
            for i, q in enumerate(questions):
                result = ai.grade_answer(
                    q.get("question", ""), q.get("answer", ""), answers[i] if i < len(answers) else "",
                    q.get("type", "short_answer"),
                )
                feedback_list.append(result)
                misconceptions.append(result.get("misconception", "none"))
                total += result.get("score", 0)
            submission.score = round(total / max(len(questions), 1), 1)
            submission.feedback = json.dumps(feedback_list)
            submission.misconceptions = json.dumps(misconceptions)
            submission.status = "graded"
            submission.graded_at = datetime.now(timezone.utc)
            current_user.add_xp(30)
        except Exception:
            current_app.logger.exception("Auto-grading submission failed")
        db.session.commit()
        flash("Submitted! Grok graded it instantly.", "success")
        return redirect(url_for("student_assignment_detail", assignment_id=assignment.id))

    return render_template(
        "student/assignment_detail.html", assignment=assignment, classroom=classroom, submission=existing
    )


@app.route("/student/revision")
@login_required
def student_revision():
    require_role("student")
    profile = m.LearningProfile.query.filter_by(student_id=current_user.id).first()
    classroom_ids = [e.classroom_id for e in current_user.enrollments]
    recent_lessons = (
        m.LessonProgress.query.filter_by(student_id=current_user.id, completed=True)
        .order_by(m.LessonProgress.completed_at.desc()).limit(8).all()
    )
    return render_template("student/revision.html", profile=profile, recent_lessons=recent_lessons)


@app.route("/student/messages", methods=["GET", "POST"])
@login_required
def student_messages():
    require_role("student")
    classroom_ids = [e.classroom_id for e in current_user.enrollments]

    if request.method == "POST":
        classroom_id = int(request.form.get("classroom_id"))
        content = request.form.get("content", "").strip()
        if content and classroom_id in classroom_ids:
            classroom = db.get_or_404(m.Classroom, classroom_id)
            db.session.add(m.Message(
                classroom_id=classroom_id, sender_id=current_user.id,
                recipient_id=classroom.teacher_id, content=content,
            ))
            db.session.commit()
        return redirect(url_for("student_messages"))

    messages = (
        m.Message.query.filter(m.Message.classroom_id.in_(classroom_ids)).order_by(
            m.Message.created_at.desc()
        ).all() if classroom_ids else []
    )
    classrooms = [e.classroom for e in current_user.enrollments]
    return render_template("student/messages.html", messages=messages, classrooms=classrooms)


@app.route("/student/tutor")
@login_required
def student_tutor():
    require_role("student")
    history = m.AIChatMessage.query.filter_by(user_id=current_user.id).order_by(
        m.AIChatMessage.created_at.asc()
    ).all()
    return render_template("student/tutor.html", history=history)


@app.route("/api/tutor/chat", methods=["POST"])
@login_required
def api_tutor_chat():
    require_role("student")
    data = request.get_json(force=True)
    message = data.get("message", "").strip()
    mode = data.get("mode", "default")
    if not message:
        return jsonify({"error": "Empty message"}), 400

    db.session.add(m.AIChatMessage(user_id=current_user.id, role="user", content=message))
    db.session.commit()

    history_rows = m.AIChatMessage.query.filter_by(user_id=current_user.id).order_by(
        m.AIChatMessage.created_at.asc()
    ).all()
    history = [{"role": r.role, "content": r.content} for r in history_rows[:-1]]

    try:
        reply = ai.tutor_reply(history, message, mode)
    except Exception as e:
        current_app.logger.exception("AI tutor call failed")
        reply = "Sorry, the AI tutor hit a snag. Please try again in a moment."

    db.session.add(m.AIChatMessage(user_id=current_user.id, role="assistant", content=reply))
    current_user.add_xp(5)
    db.session.commit()

    return jsonify({"reply": reply})


@app.route("/student/progress")
@login_required
def student_progress():
    require_role("student")
    enrollments = current_user.enrollments
    submissions = m.Submission.query.filter_by(student_id=current_user.id).order_by(
        m.Submission.submitted_at.asc()
    ).all()
    profile = m.LearningProfile.query.filter_by(student_id=current_user.id).first()
    return render_template(
        "student/progress.html", enrollments=enrollments, submissions=submissions, profile=profile
    )


@app.route("/student/achievements")
@login_required
def student_achievements():
    require_role("student")
    achievements = m.Achievement.query.filter_by(student_id=current_user.id).order_by(
        m.Achievement.earned_at.desc()
    ).all()
    return render_template("student/achievements.html", achievements=achievements)


# ---------------------------------------------------------------------------
# Settings (shared)
# ---------------------------------------------------------------------------
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "profile":
            current_user.name = request.form.get("name", current_user.name)
            db.session.commit()
            flash("Profile updated.", "success")
        elif form_type == "password":
            current_pw = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            if bcrypt.check_password_hash(current_user.password_hash, current_pw):
                current_user.password_hash = bcrypt.generate_password_hash(new_pw).decode("utf-8")
                db.session.commit()
                flash("Password changed.", "success")
            else:
                flash("Current password is incorrect.", "error")
        elif form_type == "theme":
            current_user.theme = request.form.get("theme", "light")
            db.session.commit()
        return redirect(url_for("settings"))

    return render_template("shared/settings.html")


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
@app.errorhandler(403)
def forbidden(e):
    return render_template("shared/error.html", code=403, message="You don't have access to that page."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("shared/error.html", code=404, message="That page doesn't exist."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("shared/error.html", code=500, message="Something went wrong on our end."), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)