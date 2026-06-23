"""
dashboard/routes.py
─────────────────────────────────────────────────────────────────────────────
Flask routes for the BMO Parent Dashboard.
All data endpoints return JSON so the frontend can chart them with Chart.js.
"""

from flask import Blueprint, render_template, jsonify, request
from robot.profiles.child_profile  import ChildProfileManager
from robot.analytics.reporter      import AnalyticsReporter

bp = Blueprint("dashboard", __name__)

_profiles  = ChildProfileManager()
_reporter  = AnalyticsReporter()


# ── HTML views ────────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    profiles = _profiles.list_profiles()
    return render_template("index.html", profiles=profiles)


# ── Child profile endpoints ───────────────────────────────────────────────────

@bp.route("/api/children")
def get_children():
    return jsonify(_profiles.list_profiles())


@bp.route("/api/children/<int:child_id>")
def get_child(child_id: int):
    p = _profiles.get_profile(child_id)
    return jsonify(p) if p else (jsonify({"error": "Not found"}), 404)


# ── Dashboard overview ────────────────────────────────────────────────────────

@bp.route("/api/children/<int:child_id>/summary")
def get_summary(child_id: int):
    return jsonify(_reporter.get_child_summary(child_id))


# ── Game performance ──────────────────────────────────────────────────────────

@bp.route("/api/children/<int:child_id>/games")
def get_games(child_id: int):
    return jsonify(_reporter.get_game_performance(child_id))


# ── Skill domain scores ───────────────────────────────────────────────────────

@bp.route("/api/children/<int:child_id>/skills")
def get_skills(child_id: int):
    return jsonify(_reporter.get_domain_scores(child_id))


# ── Progress trend (line chart data) ─────────────────────────────────────────

@bp.route("/api/children/<int:child_id>/trend")
def get_trend(child_id: int):
    days = int(request.args.get("days", 30))
    return jsonify(_reporter.get_score_trend(child_id, days=days))


# ── Emotion distribution (pie/bar chart data) ────────────────────────────────

@bp.route("/api/children/<int:child_id>/emotions")
def get_emotions(child_id: int):
    days = int(request.args.get("days", 7))
    return jsonify({
        "distribution": _reporter.get_emotion_distribution(child_id, days=days),
    })


@bp.route("/api/children/<int:child_id>/sessions/<int:session_id>/emotions")
def get_session_emotions(child_id: int, session_id: int):
    return jsonify(_reporter.get_emotion_timeline(child_id, session_id))


# ── Session history ───────────────────────────────────────────────────────────

@bp.route("/api/children/<int:child_id>/sessions")
def get_sessions(child_id: int):
    limit = int(request.args.get("limit", 20))
    return jsonify(_reporter.get_session_history(child_id, limit=limit))


# ── Interest map ──────────────────────────────────────────────────────────────

@bp.route("/api/children/<int:child_id>/interests")
def get_interests(child_id: int):
    return jsonify(_reporter.get_interest_distribution(child_id))


# ── Combined analytics (single call for dashboard page) ──────────────────────

@bp.route("/api/children/<int:child_id>/analytics")
def get_analytics(child_id: int):
    """One-shot endpoint returning everything the dashboard needs."""
    return jsonify({
        "summary":       _reporter.get_child_summary(child_id),
        "skills":        _reporter.get_domain_scores(child_id),
        "games":         _reporter.get_game_performance(child_id),
        "trend":         _reporter.get_score_trend(child_id, days=30),
        "emotions":      _reporter.get_emotion_distribution(child_id, days=7),
        "sessions":      _reporter.get_session_history(child_id, limit=10),
        "interests":     _reporter.get_interest_distribution(child_id),
    })
