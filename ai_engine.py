"""AI engine for DROP.

Every AI feature in the product — course generation, lesson authoring,
the AI tutor, auto-grading, misconception detection, and analytics
insights — flows through this module, calling whatever OpenAI-compatible
Chat Completions endpoint is configured via XAI_API_KEY / XAI_BASE_URL /
GROK_MODEL. This currently points at Groq (api.groq.com), but works the
same way with xAI, or any other OpenAI-compatible provider — just change
the .env values.

If no XAI_API_KEY is configured, functions fall back to deterministic
mock content so the whole product still runs end-to-end for a demo.
"""
import json
import re
from flask import current_app

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None


def _client():
    """Build an OpenAI-compatible client pointed at xAI's Grok endpoint."""
    api_key = current_app.config.get("XAI_API_KEY")
    if not api_key or OpenAI is None:
        return None
    return OpenAI(api_key=api_key, base_url=current_app.config.get("XAI_BASE_URL"))


def _model():
    return current_app.config.get("GROK_MODEL", "openai/gpt-oss-120b")


def _extract_json(text):
    """Grok sometimes wraps JSON in markdown fences or adds preamble text."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def _chat(system_prompt, user_prompt, json_mode=True, max_tokens=4000):
    """Call Grok with a system+user prompt. Returns parsed JSON dict or raw text."""
    client = _client()
    if client is None:
        return None  # caller falls back to mock content

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    kwargs = dict(model=_model(), messages=messages, max_tokens=max_tokens, temperature=0.4)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content
    if json_mode:
        return _extract_json(content)
    return content


# ---------------------------------------------------------------------------
# Course generation
#
# NOTE: Course content is rich (thorough notes, worked examples, quizzes,
# etc. per lesson), so generating an entire multi-week course in a single
# completion easily blows past provider rate limits (e.g. Groq's on-demand
# tier caps requests at 8000 tokens/minute — prompt + completion combined).
# Instead we generate the course PLAN first (cheap), then generate each
# WEEK separately (bounded size regardless of duration_weeks), then the
# midterm/final exam last. This keeps every single call small and safe,
# and means one failed week doesn't take down the whole course.
# ---------------------------------------------------------------------------

PLAN_SYSTEM_PROMPT = """You are DROP's AI curriculum architect. Produce a high-level
plan for a course — NOT full lesson content yet. Always respond with a single valid
JSON object and nothing else, matching exactly this schema:

{
  "overview": "string, short course overview (2-3 sentences)",
  "weeks": [
    {"number": 1, "title": "string", "summary": "string, 1-2 sentences", "lesson_titles": ["string", ...]}
  ]
}

Include 2-3 lesson_titles per week. Keep it concise — this is just an outline."""

WEEK_SYSTEM_PROMPT = """You are DROP's AI curriculum architect, writing the full content
for ONE week of a course, given the overall course plan for context. Always respond
with a single valid JSON object and nothing else, matching exactly this schema:

{
  "lessons": [
    {
      "title": "string",
      "objectives": ["string", ...],
      "notes": "markdown lecture notes, thorough, at least 4 paragraphs",
      "definitions": ["Term: definition", ...],
      "examples": "markdown, at least 2 worked examples",
      "applications": "markdown, real life applications",
      "common_mistakes": ["string", ...],
      "practice": [{"question": "string", "answer": "string"}, ...],
      "revision": "markdown revision summary",
      "summary": "markdown short summary",
      "homework": ["string", ...],
      "quiz": [{"question": "string", "type": "mcq", "options": ["A","B","C","D"], "answer": "A"}]
    }
  ],
  "weekly_test": {"questions": [{"question": "string", "type": "mcq|short_answer", "options": [], "answer": "string"}]}
}

Generate real, subject-accurate educational content, not placeholders. Write ONLY
the lessons for the requested week — do not repeat other weeks."""

EXAMS_SYSTEM_PROMPT = """You are DROP's AI curriculum architect, writing the midterm and
final exam for a course, given its plan for context. Always respond with a single valid
JSON object and nothing else, matching exactly this schema:

{
  "midterm": {"questions": [{"question": "string", "type": "mcq|short_answer", "options": [], "answer": "string"}]},
  "final_exam": {"questions": [{"question": "string", "type": "mcq|short_answer|essay", "options": [], "answer": "string"}]}
}

Aim for 6-10 midterm questions and 8-12 final exam questions, covering the whole
course plan. Generate real, subject-accurate content, not placeholders."""


def generate_course(subject, duration_weeks, target_grade, syllabus_text=""):
    """Generate a complete course incrementally: plan -> each week -> exams.

    Each API call is kept small and bounded regardless of duration_weeks, so we
    don't hit provider TPM rate limits on longer courses. If the AI client isn't
    configured at all, falls back to fully deterministic mock content. If an
    individual week or the exams call fails partway through, that piece falls
    back to mock content rather than failing the whole course.
    """
    if _client() is None:
        return _mock_course(subject, duration_weeks, target_grade)

    plan_prompt = (
        f"Design a plan for a complete {duration_weeks}-week course.\n"
        f"Subject: {subject}\n"
        f"Target grade/level: {target_grade or 'general'}\n"
        f"Include 2-3 lessons per week.\n"
    )
    if syllabus_text:
        plan_prompt += f"\nBase it on this syllabus:\n{syllabus_text[:4000]}"

    plan = _chat(PLAN_SYSTEM_PROMPT, plan_prompt, json_mode=True, max_tokens=1500)
    if plan is None or not plan.get("weeks"):
        return _mock_course(subject, duration_weeks, target_grade)

    plan_context = json.dumps({"overview": plan.get("overview", ""), "weeks": plan["weeks"]})

    weeks = []
    for week_plan in plan["weeks"]:
        week_prompt = (
            f"Course plan (for context):\n{plan_context[:3000]}\n\n"
            f"Now write the full content for week {week_plan.get('number')}: "
            f"\"{week_plan.get('title', '')}\" — {week_plan.get('summary', '')}\n"
            f"Lessons to cover: {', '.join(week_plan.get('lesson_titles', [])) or '(use your judgment)'}"
        )
        try:
            week_content = _chat(WEEK_SYSTEM_PROMPT, week_prompt, json_mode=True, max_tokens=3000)
        except Exception:
            week_content = None

        if week_content is None or not week_content.get("lessons"):
            mock_week = _mock_course(subject, 1, target_grade)["weeks"][0]
            week_content = {"lessons": mock_week["lessons"], "weekly_test": mock_week["weekly_test"]}

        weeks.append({
            "number": week_plan.get("number"),
            "title": week_plan.get("title", f"Week {week_plan.get('number')}"),
            "summary": week_plan.get("summary", ""),
            "lessons": week_content["lessons"],
            "weekly_test": week_content.get("weekly_test", {"questions": []}),
        })

    exams_prompt = f"Course plan (for context):\n{plan_context[:3000]}"
    try:
        exams = _chat(EXAMS_SYSTEM_PROMPT, exams_prompt, json_mode=True, max_tokens=2000)
    except Exception:
        exams = None
    if exams is None:
        mock = _mock_course(subject, 1, target_grade)
        exams = {"midterm": mock["midterm"], "final_exam": mock["final_exam"]}

    return {
        "overview": plan.get("overview", f"A {duration_weeks}-week course on {subject}."),
        "weeks": weeks,
        "midterm": exams.get("midterm", {"questions": []}),
        "final_exam": exams.get("final_exam", {"questions": []}),
    }


def _mock_course(subject, duration_weeks, target_grade):
    """Deterministic offline fallback so the product works without an API key."""
    weeks = []
    for w in range(1, duration_weeks + 1):
        lessons = []
        for l in range(1, 3):
            lessons.append({
                "title": f"{subject} — Week {w}, Lesson {l}",
                "objectives": [f"Understand core concept {l} of week {w}", "Apply it to a real example"],
                "notes": (
                    f"This lesson introduces key ideas in {subject} for week {w}. "
                    f"We build foundational understanding step by step, connecting new "
                    f"vocabulary to concepts already covered. By the end, you should be able "
                    f"to explain the idea in your own words and apply it to a new problem."
                ),
                "definitions": [f"Key term {l}: a foundational idea in {subject}"],
                "examples": f"Example 1: a worked problem in {subject}.\n\nExample 2: a second worked problem.",
                "applications": f"This concept shows up in everyday uses of {subject}, from planning to problem solving.",
                "common_mistakes": ["Confusing related terms", "Skipping steps under time pressure"],
                "practice": [{"question": f"Practice question {l} for week {w}", "answer": "See lecture notes"}],
                "revision": f"Review the definitions and worked examples from week {w} before moving on.",
                "summary": f"Week {w} lesson {l} covered a core building block of {subject}.",
                "homework": [f"Complete practice set {l}"],
                "quiz": [{"question": f"Quick check {l}", "type": "mcq", "options": ["A", "B", "C", "D"], "answer": "A"}],
            })
        weeks.append({
            "number": w,
            "title": f"Week {w}: {subject} Foundations {w}",
            "summary": f"Building blocks of {subject}, part {w}.",
            "lessons": lessons,
            "weekly_test": {"questions": [{"question": f"Week {w} test question", "type": "short_answer", "options": [], "answer": "Model answer"}]},
        })
    return {
        "overview": f"A {duration_weeks}-week AI-generated {subject} course targeting {target_grade or 'a general level'}.",
        "weeks": weeks,
        "midterm": {"questions": [{"question": "Midterm question", "type": "short_answer", "options": [], "answer": "Model answer"}]},
        "final_exam": {"questions": [{"question": "Final exam question", "type": "essay", "options": [], "answer": "Model answer"}]},
    }


# ---------------------------------------------------------------------------
# Solo study course generation (from a topic or uploaded document text)
# ---------------------------------------------------------------------------
def generate_solo_course(topic, source_text=""):
    return generate_course(topic, 4, "self-study", source_text)


# ---------------------------------------------------------------------------
# Assignment / test question generation (grounded in the teacher's uploaded
# syllabus/notes for that classroom, not a whole separate generated course)
# ---------------------------------------------------------------------------
ASSIGNMENT_SYSTEM_PROMPT = """You are DROP's assignment writer. Generate a set of
questions for a single assignment/test, tightly matched to the given title,
description, and kind. If source material is provided, base the questions
directly on it — reuse its terminology, examples, and specific content rather
than generic textbook questions. Always respond with a single valid JSON object:
{"questions": [{"question": "string", "type": "mcq|short_answer|essay",
"options": ["A. ...", "B. ...", "C. ...", "D. ..."] or [], "answer": "string"}]}
Generate exactly the requested number of questions, difficulty appropriate to
the kind (quick check for classwork, more rigorous for a test/exam)."""


def generate_assignment_questions(subject, title, description, kind, source_text="", count=5):
    prompt = (
        f"Subject: {subject}\n"
        f"Assignment title: {title}\n"
        f"Kind: {kind}\n"
        f"Description/instructions from the teacher: {description or '(none given)'}\n"
        f"Number of questions: {count}\n"
    )
    if source_text:
        prompt += f"\nBase the questions closely on this uploaded classroom material:\n{source_text[:6000]}"

    result = _chat(ASSIGNMENT_SYSTEM_PROMPT, prompt, json_mode=True, max_tokens=3000)
    if result is None:
        return _mock_assignment_questions(title, count)
    return result.get("questions", []) or _mock_assignment_questions(title, count)


def _mock_assignment_questions(title, count=5):
    return [
        {"question": f"Question {i + 1} about {title}", "type": "short_answer",
         "options": [], "answer": "Model answer"}
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# AI Tutor
# ---------------------------------------------------------------------------
TUTOR_SYSTEM_PROMPT = """You are the DROP AI Tutor: warm, encouraging, and extremely clear.
You teach one concept at a time, check understanding, and adapt your explanation style
(simple, visual, mathematical, or "explain like I'm 10") based on what the student asks for.
Keep answers focused and well-structured with short paragraphs, examples, and, when useful,
a short follow-up question to check understanding. Respond in plain text (not JSON).

FORMATTING RULES — the chat window only displays plain text, so:
- NEVER use LaTeX. No \\( \\), \\[ \\], $, $$, \\frac, \\boxed, or any other LaTeX commands or delimiters.
- Write all math in plain, calculator-style notation instead: x^x, (ln(x) + 1), sqrt(x), a/b, x^2 + 3x - 5.
- Do not use Markdown headers (#, ##), horizontal rules (---), or emoji numbering (1️⃣, 2️⃣). Use plain
  numbered lists (1., 2., 3.) or short paragraphs instead.
- Bold text sparingly with *asterisks* only if it aids clarity, not for decoration.
- Keep it readable as plain chat text — no tables, no boxed answers, no decorative symbols like ✅ or ---."""


def tutor_reply(history, student_message, mode="default"):
    """history: list of {'role': 'user'|'assistant', 'content': str}"""
    client = _client()
    if client is None:
        return _mock_tutor_reply(student_message, mode)

    mode_hints = {
        "simplify": "Simplify your explanation as much as possible.",
        "eli10": "Explain like the student is 10 years old, using simple analogies.",
        "visual": "Describe it visually, as if sketching a diagram in words (still no LaTeX).",
        "math": "Be precise and rigorous, but keep all notation in plain text (x^2, not LaTeX).",
        "examples": "Focus on generating several worked examples.",
        "default": "",
    }
    system = TUTOR_SYSTEM_PROMPT
    if mode_hints.get(mode):
        system += f"\n\nSpecial instruction for this reply: {mode_hints[mode]}"

    messages = [{"role": "system", "content": system}]
    messages.extend(history[-20:])
    messages.append({"role": "user", "content": student_message})

    response = client.chat.completions.create(
        model=_model(), messages=messages, max_tokens=1200, temperature=0.6,
    )
    return response.choices[0].message.content


def _mock_tutor_reply(student_message, mode):
    return (
        f"(Offline demo tutor — connect XAI_API_KEY for real Grok answers)\n\n"
        f"Great question about: \"{student_message}\". Here's a step-by-step explanation:\n"
        f"1. Let's identify what the question is really asking.\n"
        f"2. We break the idea into smaller parts.\n"
        f"3. We connect it to something you already know.\n"
        f"4. We check with a quick example.\n\n"
        f"Want me to simplify this further, give more examples, or quiz you on it?"
    )


# ---------------------------------------------------------------------------
# Auto-grading & the "Understanding Engine"
# ---------------------------------------------------------------------------
GRADING_SYSTEM_PROMPT = """You are DROP's auto-grader and Understanding Engine. Grade the
student's answer against the reference answer/rubric. Never just mark right or wrong —
diagnose WHY a wrong answer is wrong. Respond with a single JSON object:
{
  "score": 0-100,
  "is_correct": true/false,
  "feedback": "specific, encouraging feedback in 2-3 sentences",
  "misconception": "one of: none, calculation_error, concept_misunderstanding, guess,
                     carelessness, formula_forgotten, vocabulary_misunderstanding",
  "reteach_tip": "one short sentence on what to review"
}"""


def grade_answer(question, reference_answer, student_answer, question_type="short_answer"):
    prompt = (
        f"Question type: {question_type}\n"
        f"Question: {question}\n"
        f"Reference answer: {reference_answer}\n"
        f"Student answer: {student_answer}\n"
    )
    result = _chat(GRADING_SYSTEM_PROMPT, prompt, json_mode=True, max_tokens=500)
    if result is None:
        return _mock_grade(reference_answer, student_answer)
    return result


def _mock_grade(reference_answer, student_answer):
    correct = str(student_answer).strip().lower() == str(reference_answer).strip().lower()
    return {
        "score": 100 if correct else 40,
        "is_correct": correct,
        "feedback": "Nice work, that matches the expected answer." if correct else
                    "Not quite — review the lesson notes and try comparing your steps to the worked example.",
        "misconception": "none" if correct else "concept_misunderstanding",
        "reteach_tip": "" if correct else "Revisit the definitions section of this lesson.",
    }


# ---------------------------------------------------------------------------
# Analytics insights
# ---------------------------------------------------------------------------
INSIGHTS_SYSTEM_PROMPT = """You are DROP's classroom analytics assistant. Given aggregate
class data, produce concise, actionable insights for a teacher. Respond with JSON:
{"insights": ["string", "string", "string"]}"""


def generate_class_insights(stats_summary):
    result = _chat(INSIGHTS_SYSTEM_PROMPT, json.dumps(stats_summary), json_mode=True, max_tokens=600)
    if result is None:
        return {"insights": [
            "Average scores are steady — consider adding a stretch challenge for top performers.",
            "A few students have incomplete assignments this week; a reminder nudge could help.",
            "Revisit topics with the lowest quiz accuracy in the next class session.",
        ]}
    return result