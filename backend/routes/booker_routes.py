from __future__ import annotations

import json
import uuid
from datetime import datetime
from flask import Blueprint, current_app, jsonify, request
from services.ai_showrunner_service import AIShowrunnerService
from services.post_show_fallout_service import PostShowFalloutService
from services.llm_provider import LLMProvider, LLMProposalService
from services.talent_chat_service import TalentChatService

booker_bp = Blueprint('booker', __name__)

PERSONALITIES = {"veteran", "marketer", "historian", "anarchist"}
PRIORITIES = {"urgent", "opportunity", "spark"}
STATUSES = {"open", "accepted", "rejected", "modified", "pinned", "dismissed"}


def get_database():
    return current_app.config['DATABASE']


def get_universe():
    return current_app.config.get('UNIVERSE')


def get_showrunner():
    service = current_app.config.get('AI_SHOWRUNNER_SERVICE')
    if service is None:
        service = AIShowrunnerService(get_database())
        current_app.config['AI_SHOWRUNNER_SERVICE'] = service
    return service


def get_post_show_fallout():
    service = current_app.config.get('POST_SHOW_FALLOUT_SERVICE')
    if service is None:
        service = PostShowFalloutService(get_database())
        current_app.config['POST_SHOW_FALLOUT_SERVICE'] = service
    return service


def get_llm_proposal_service():
    service = current_app.config.get('LLM_PROPOSAL_SERVICE')
    if service is None:
        service = LLMProposalService(get_showrunner(), LLMProvider())
        current_app.config['LLM_PROPOSAL_SERVICE'] = service
    return service


def get_talent_chat_service():
    service = current_app.config.get('TALENT_CHAT_SERVICE')
    if service is None:
        service = TalentChatService(get_database(), LLMProvider())
        current_app.config['TALENT_CHAT_SERVICE'] = service
    return service

def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def _coerce_int(value, fallback: int) -> int:
    if value in (None, ""):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _request_year_week(data: dict, state: dict) -> tuple[int, int]:
    year = _coerce_int(data.get('year'), _coerce_int((state or {}).get('current_year'), 1))
    week = _coerce_int(data.get('week'), _coerce_int((state or {}).get('current_week'), 1))
    return year, week


def _ensure_tables(db):
    c = db.conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS creative_assistant_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            personality TEXT NOT NULL DEFAULT 'veteran',
            risk_tolerance REAL NOT NULL DEFAULT 0.5,
            storytelling_tempo REAL NOT NULL DEFAULT 0.5,
            updated_at TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS booker_suggestions (
            suggestion_id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            priority TEXT NOT NULL,
            headline TEXT NOT NULL,
            rationale TEXT NOT NULL,
            options_json TEXT NOT NULL,
            projections_json TEXT NOT NULL,
            context_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            response_reason TEXT,
            counter_pitch TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS creative_notebook_entries (
            entry_id TEXT PRIMARY KEY,
            suggestion_id TEXT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            tag TEXT,
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (suggestion_id) REFERENCES booker_suggestions(suggestion_id)
        )
    ''')
    db.conn.commit()


def _row_to_suggestion(row):
    d = dict(row)
    d['options'] = json.loads(d.pop('options_json'))
    d['projections'] = json.loads(d.pop('projections_json'))
    d['context'] = json.loads(d.pop('context_json'))
    return d

@booker_bp.route('/api/booker/profile', methods=['GET', 'PUT'])
def booker_profile():
    db = get_database()
    _ensure_tables(db)
    c = db.conn.cursor()
    if request.method == 'PUT':
        payload = request.get_json(silent=True) or {}
        personality = str(payload.get('personality', 'veteran')).lower()
        if personality not in PERSONALITIES:
            return jsonify({'error': 'Invalid personality'}), 400
        risk = max(0.0, min(1.0, float(payload.get('risk_tolerance', 0.5))))
        tempo = max(0.0, min(1.0, float(payload.get('storytelling_tempo', 0.5))))
        c.execute('''
            INSERT INTO creative_assistant_profile (id, personality, risk_tolerance, storytelling_tempo, updated_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET personality=excluded.personality,
                risk_tolerance=excluded.risk_tolerance, storytelling_tempo=excluded.storytelling_tempo,
                updated_at=excluded.updated_at
        ''', (personality, risk, tempo, _now_iso()))
        db.conn.commit()

    row = c.execute('SELECT * FROM creative_assistant_profile WHERE id = 1').fetchone()
    if not row:
        c.execute('INSERT INTO creative_assistant_profile (id, personality, risk_tolerance, storytelling_tempo, updated_at) VALUES (1, ?, ?, ?, ?)',
                  ('veteran', 0.5, 0.5, _now_iso()))
        db.conn.commit()
        row = c.execute('SELECT * FROM creative_assistant_profile WHERE id = 1').fetchone()
    return jsonify(dict(row))

@booker_bp.route('/api/booker/suggestions', methods=['GET', 'POST'])
def suggestions():
    db = get_database()
    _ensure_tables(db)
    c = db.conn.cursor()

    if request.method == 'POST':
        p = request.get_json(silent=True) or {}
        priority = str(p.get('priority', 'opportunity')).lower()
        status = str(p.get('status', 'open')).lower()
        if priority not in PRIORITIES or status not in STATUSES:
            return jsonify({'error': 'Invalid priority or status'}), 400

        sid = f"sg_{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        headline = p.get('headline', 'New suggestion')
        rationale = p.get('rationale', '')
        c.execute('''
            INSERT INTO booker_suggestions (
                suggestion_id, category, priority, headline, rationale, options_json,
                projections_json, context_json, status, response_reason, counter_pitch,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            sid,
            p.get('category', 'creative-spark'),
            priority,
            headline,
            rationale,
            json.dumps(p.get('options', [])),
            json.dumps(p.get('projections', {})),
            json.dumps(p.get('context', {})),
            status,
            p.get('response_reason'),
            p.get('counter_pitch'),
            now,
            now,
        ))
        db.conn.commit()
        state = db.get_game_state() if hasattr(db, 'get_game_state') else {}
        year, week = _request_year_week(p, state)
        approval = get_showrunner().queue_external_item(
            year=year,
            week=week,
            source_type='booker_suggestion',
            source_id=sid,
            category=str(p.get('category', 'creative-spark')).lower().replace(' ', '_'),
            priority=priority,
            title=headline,
            summary=rationale or 'Creative assistant suggestion awaiting review.',
            recommendation={
                'legacy_suggestion_id': sid,
                'options': p.get('options', []),
                'projections': p.get('projections', {}),
                'context': p.get('context', {}),
            },
            policy='ask',
            auto_week=None,
        )
        row = c.execute('SELECT * FROM booker_suggestions WHERE suggestion_id = ?', (sid,)).fetchone()
        response = _row_to_suggestion(row)
        response['approval'] = approval
        return jsonify(response), 201

    status = request.args.get('status')
    query = 'SELECT * FROM booker_suggestions'
    params = []
    if status:
        query += ' WHERE status = ?'
        params.append(status)
    query += ' ORDER BY created_at DESC'
    rows = c.execute(query, params).fetchall()
    return jsonify({'total': len(rows), 'suggestions': [_row_to_suggestion(r) for r in rows]})

@booker_bp.route('/api/booker/suggestions/<suggestion_id>/respond', methods=['POST'])
def respond_suggestion(suggestion_id):
    db = get_database()
    _ensure_tables(db)
    c = db.conn.cursor()
    p = request.get_json(silent=True) or {}
    action = str(p.get('action', '')).lower()
    mapping = {'accept': 'accepted', 'reject': 'rejected', 'modify': 'modified', 'pin': 'pinned', 'dismiss': 'dismissed'}
    if action not in mapping:
        return jsonify({'error': 'Invalid action'}), 400
    new_status = mapping[action]
    c.execute('''
        UPDATE booker_suggestions
        SET status = ?, response_reason = ?, counter_pitch = ?, updated_at = ?
        WHERE suggestion_id = ?
    ''', (new_status, p.get('reason'), p.get('counter_pitch'), _now_iso(), suggestion_id))
    if c.rowcount == 0:
        return jsonify({'error': 'Suggestion not found'}), 404

    title = p.get('notebook_title') or f"{action.title()}: {suggestion_id}"
    body = p.get('notebook_body') or p.get('note') or 'Player response captured.'
    c.execute('''
        INSERT INTO creative_notebook_entries (entry_id, suggestion_id, title, body, tag, pinned, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (f"nb_{uuid.uuid4().hex[:12]}", suggestion_id, title, body, p.get('tag'), 1 if new_status == 'pinned' else 0, _now_iso()))
    db.conn.commit()
    return jsonify({'success': True, 'status': new_status})

@booker_bp.route('/api/booker/notebook', methods=['GET'])
def notebook():
    db = get_database()
    _ensure_tables(db)
    c = db.conn.cursor()
    rows = c.execute('SELECT * FROM creative_notebook_entries ORDER BY created_at DESC').fetchall()
    return jsonify({'total': len(rows), 'entries': [dict(r) for r in rows]})



@booker_bp.route('/api/booker/inbox', methods=['GET'])
def booker_inbox():
    try:
        status = request.args.get('status', 'pending')
        if status in ('all', '*'):
            status = None
        limit = _coerce_int(request.args.get('limit'), 100)
        category = request.args.get('category')
        return jsonify(get_showrunner().inbox(status=status, limit=limit, category=category))
    except Exception as exc:
        current_app.logger.exception("Booker inbox failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/llm/status', methods=['GET'])
def llm_status():
    try:
        return jsonify(get_llm_proposal_service().provider_status())
    except Exception as exc:
        current_app.logger.exception("LLM status failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/llm/proposals', methods=['POST'])
def create_llm_proposal():
    try:
        data = request.get_json(silent=True) or {}
        prompt = str(data.get('prompt') or data.get('message') or '').strip()
        if not prompt:
            return jsonify({'error': 'prompt is required'}), 422
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(data, state)
        context = data.get('context') or {}
        if not isinstance(context, dict):
            return jsonify({'error': 'context must be an object'}), 422
        result = get_llm_proposal_service().create_pitch(prompt, context=context, year=year, week=week)
        return jsonify(result), 201
    except Exception as exc:
        current_app.logger.exception("LLM proposal failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/dashboard', methods=['GET'])
def showrunner_dashboard():
    try:
        return jsonify(get_showrunner().dashboard())
    except Exception as exc:
        current_app.logger.exception("Showrunner dashboard failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/weekly', methods=['POST'])
def run_showrunner_weekly():
    try:
        data = request.get_json(silent=True) or {}
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(data, state)
        result = get_showrunner().run_weekly(
            year,
            week,
            universe=get_universe(),
            seed=data.get('seed'),
            force=bool(data.get('force', False)),
            autonomy_level=str(data.get('autonomy_level', 'balanced')).lower(),
        )
        return jsonify(result)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 422
    except Exception as exc:
        current_app.logger.exception("Showrunner weekly run failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/latest-booking-draft', methods=['GET'])
def latest_showrunner_booking_draft():
    try:
        return jsonify(get_showrunner().latest_booking_draft())
    except Exception as exc:
        current_app.logger.exception("Showrunner latest booking draft failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/mitb/card', methods=['GET'])
def fortunes_ladder_card():
    """Get the booked Men's and Women's Money in the Bank ladder match fields."""
    try:
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(request.args.to_dict(), state)
        roster = get_showrunner().get_roster_snapshot("Cross-Brand")
        result = get_showrunner().book_fortunes_ladder_card(year, week, roster, get_universe())
        if result.get('error'):
            return jsonify(result), 422
        return jsonify(result)
    except Exception as exc:
        current_app.logger.exception("Fortune's Ladder card build failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/mitb/card/replace', methods=['POST'])
def replace_mitb_card_participant():
    """Swap one wrestler out of a persisted (not-yet-simulated) MITB ladder field."""
    try:
        data = request.get_json(silent=True) or {}
        division = str(data.get('division', '')).lower()
        if division not in ('mens', 'womens'):
            return jsonify({'error': "division must be 'mens' or 'womens'"}), 422
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(data, state)
        old_id = data.get('old_wrestler_id')
        new_id = data.get('new_wrestler_id')
        new_name = data.get('new_wrestler_name')
        if not (old_id and new_id and new_name):
            return jsonify({'error': 'old_wrestler_id, new_wrestler_id, and new_wrestler_name are required'}), 422
        result = get_showrunner().replace_mitb_card_participant(division, year, week, old_id, new_id, new_name)
        if result.get('error'):
            return jsonify(result), 422
        return jsonify(result)
    except Exception as exc:
        current_app.logger.exception("MITB card participant replace failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/mitb/card/confirm', methods=['POST'])
def confirm_mitb_card():
    """Lock in both divisions' MITB ladder fields."""
    try:
        data = request.get_json(silent=True) or {}
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(data, state)
        return jsonify(get_showrunner().confirm_mitb_card(year, week))
    except Exception as exc:
        current_app.logger.exception("MITB card confirm failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/mitb/resolve', methods=['POST'])
def resolve_mitb_ladder_match():
    """Award the briefcase to the winner of a simulated MITB ladder match."""
    try:
        data = request.get_json(silent=True) or {}
        division = str(data.get('division', '')).lower()
        if division not in ('mens', 'womens'):
            return jsonify({'error': "division must be 'mens' or 'womens'"}), 422
        winner_id = data.get('winner_id')
        winner_name = data.get('winner_name')
        if not winner_id or not winner_name:
            return jsonify({'error': 'winner_id and winner_name are required'}), 422
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(data, state)
        result = get_showrunner().resolve_mitb_winner(
            division, winner_id, winner_name, year, week,
            cash_in_window_weeks=_coerce_int(data.get('cash_in_window_weeks'), 52),
        )
        return jsonify(result)
    except Exception as exc:
        current_app.logger.exception("MITB winner resolution failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/mitb/briefcases', methods=['GET'])
def list_mitb_briefcases():
    try:
        status = request.args.get('status')
        limit = _coerce_int(request.args.get('limit'), 10)
        return jsonify({'briefcases': get_showrunner().list_mitb_briefcases(status, limit)})
    except Exception as exc:
        current_app.logger.exception("MITB briefcase listing failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/mitb/cash-in/schedule', methods=['POST'])
def schedule_mitb_cash_in():
    """
    Schedule an active briefcase's cash-in for a specific future week/title
    instead of resolving it immediately. The AI Showrunner will notify the
    player as that week approaches and book the Championship match as that
    show's main event.
    """
    try:
        data = request.get_json(silent=True) or {}
        briefcase_id = data.get('briefcase_id')
        target_title_id = data.get('target_title_id')
        scheduled_year = data.get('scheduled_year')
        scheduled_week = data.get('scheduled_week')
        if not (briefcase_id and target_title_id and scheduled_year and scheduled_week):
            return jsonify({'error': 'briefcase_id, target_title_id, scheduled_year, and scheduled_week are required'}), 422

        universe = get_universe()
        championship = universe.get_championship_by_id(target_title_id) if universe else None
        if not championship:
            return jsonify({'error': f'championship {target_title_id} not found'}), 404
        if getattr(championship, 'title_type', None) == 'Tag Team':
            return jsonify({'error': 'Money in the Bank can only be cashed in on a singles championship'}), 422

        result = get_showrunner().schedule_mitb_cash_in(
            briefcase_id, target_title_id, championship.name,
            _coerce_int(scheduled_year, 0), _coerce_int(scheduled_week, 0),
        )
        if result.get('error'):
            return jsonify(result), 422
        return jsonify(result)
    except Exception as exc:
        current_app.logger.exception("MITB cash-in scheduling failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/mitb/cash-in', methods=['POST'])
def cash_in_mitb_briefcase():
    """
    Cash in an active briefcase against any singles champion.
    If wrestler_won is true, the title actually changes hands here.
    """
    try:
        data = request.get_json(silent=True) or {}
        briefcase_id = data.get('briefcase_id')
        target_title_id = data.get('target_title_id')
        if not briefcase_id or not target_title_id:
            return jsonify({'error': 'briefcase_id and target_title_id are required'}), 422

        universe = get_universe()
        championship = universe.get_championship_by_id(target_title_id) if universe else None
        if not championship:
            return jsonify({'error': f'championship {target_title_id} not found'}), 404
        if getattr(championship, 'title_type', None) == 'Tag Team':
            return jsonify({'error': 'Money in the Bank can only be cashed in on a singles championship'}), 422

        wrestler_won = bool(data.get('wrestler_won', False))
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(data, state)
        show_id = data.get('show_id')
        show_name = data.get('show_name', 'Fortune\'s Ladder Cash-In')

        outcome = get_showrunner().cash_in_mitb_briefcase(
            briefcase_id,
            target_title_id,
            championship.name,
            wrestler_won,
            year,
            week,
            show_id=show_id,
            show_name=show_name,
        )
        if outcome.get('error'):
            return jsonify(outcome), 422

        title_changed_hands = False
        if wrestler_won:
            new_champion_name = data.get('holder_name') or outcome.get('holder_name')
            new_champion_id = outcome.get('holder_id')
            if new_champion_id and championship.current_holder_id != new_champion_id:
                championship.award_title(
                    wrestler_id=new_champion_id,
                    wrestler_name=new_champion_name,
                    show_id=show_id,
                    show_name=show_name,
                    year=year,
                    week=week,
                )
                universe.save_championship(championship)
                title_changed_hands = True

        outcome['title_changed_hands'] = title_changed_hands
        return jsonify(outcome)
    except Exception as exc:
        current_app.logger.exception("MITB cash-in failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/war-games/plan', methods=['GET'])
def war_games_plan():
    """Get (or build) the current WarGames faction plan for the target event."""
    try:
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(request.args.to_dict(), state)
        roster = get_showrunner().get_roster_snapshot("Cross-Brand")
        universe = get_universe()
        plan = get_showrunner().get_or_build_war_games_plan(year, week, roster, universe)
        return jsonify(plan)
    except Exception as exc:
        current_app.logger.exception("WarGames plan build failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/war-games/simulate', methods=['POST'])
def simulate_war_games():
    """
    Simulate a booked WarGames plan through the dedicated engine (timed
    entries, momentum system, dramatic-beat spot library) and persist the
    result onto the plan.
    """
    try:
        data = request.get_json(silent=True) or {}
        plan_id = data.get('plan_id')
        if not plan_id:
            return jsonify({'error': 'plan_id is required'}), 422

        showrunner = get_showrunner()
        plans = showrunner.list_war_games_plans(limit=25)
        plan = next((p for p in plans if p.get('id') == plan_id), None)
        if not plan:
            return jsonify({'error': f'war games plan {plan_id} not found'}), 404

        roster = showrunner.get_roster_snapshot("Cross-Brand")
        result = showrunner.simulate_war_games_plan(
            plan,
            roster,
            booked_winner_side=data.get('booked_winner_side'),
            injured_wrestler_id=data.get('injured_wrestler_id'),
            unlikely_allies=data.get('unlikely_allies'),
        )
        if result.get('error'):
            return jsonify(result), 422
        return jsonify(result)
    except Exception as exc:
        current_app.logger.exception("WarGames simulation failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/war-games/list', methods=['GET'])
def list_war_games_plans():
    try:
        limit = _coerce_int(request.args.get('limit'), 5)
        return jsonify({'plans': get_showrunner().list_war_games_plans(limit)})
    except Exception as exc:
        current_app.logger.exception("WarGames plan listing failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/crown-tournament/build', methods=['POST'])
def build_crown_tournament():
    """
    Build a King (mens) or Queen (womens) of the Ring bracket: 4 QFs + 2 SFs
    on weekly shows, Final on the next minor PLE before Summer Slamfest.
    Not a standalone PLE.
    """
    try:
        data = request.get_json(silent=True) or {}
        tournament_type = str(data.get('tournament_type', '')).lower()
        if tournament_type not in ('king', 'queen'):
            return jsonify({'error': "tournament_type must be 'king' or 'queen'"}), 422
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(data, state)
        roster = get_showrunner().get_roster_snapshot("Cross-Brand")
        universe = get_universe()
        result = get_showrunner().build_crown_tournament(tournament_type, year, week, roster, universe)
        if result.get('error'):
            return jsonify(result), 422
        return jsonify(result)
    except Exception as exc:
        current_app.logger.exception("Crown tournament build failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/crown-tournament/resolve', methods=['POST'])
def resolve_crown_tournament_match():
    """Record a QF/SF/Final result and auto-advance the bracket."""
    try:
        data = request.get_json(silent=True) or {}
        tournament_id = data.get('tournament_id')
        round_number = _coerce_int(data.get('round_number'), None)
        match_num = _coerce_int(data.get('match_num'), None)
        winner_id = data.get('winner_id')
        winner_name = data.get('winner_name')
        if not all([tournament_id, round_number, match_num, winner_id, winner_name]):
            return jsonify({'error': 'tournament_id, round_number, match_num, winner_id, winner_name are all required'}), 422
        universe = get_universe()
        result = get_showrunner().resolve_crown_tournament_match(
            tournament_id, round_number, match_num, winner_id, winner_name, universe,
        )
        if result.get('error'):
            return jsonify(result), 422
        return jsonify(result)
    except Exception as exc:
        current_app.logger.exception("Crown tournament match resolution failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/crown-tournament/list', methods=['GET'])
def list_crown_tournaments():
    try:
        limit = _coerce_int(request.args.get('limit'), 5)
        return jsonify({'tournaments': get_showrunner().list_crown_tournaments(limit)})
    except Exception as exc:
        current_app.logger.exception("Crown tournament listing failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/dark-house-week', methods=['POST'])
def run_dark_house_week():
    try:
        data = request.get_json(silent=True) or {}
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(data, state)
        return jsonify(get_showrunner().run_dark_house_autopilot(
            year,
            week,
            universe=get_universe(),
            seed=data.get('seed'),
            force=bool(data.get('force', False)),
            autonomy_level=str(data.get('autonomy_level', 'balanced')).lower(),
        ))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 422
    except Exception as exc:
        current_app.logger.exception("Dark/house autopilot failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/promo-beats', methods=['POST'])
def generate_promo_beats():
    try:
        data = request.get_json(silent=True) or {}
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(data, state)
        return jsonify(get_showrunner().generate_promo_beats(
            year,
            week,
            show_draft=data.get('show_draft'),
            seed=data.get('seed'),
            force=bool(data.get('force', False)),
        ))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 422
    except Exception as exc:
        current_app.logger.exception("Promo beat generation failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/showrunner/live-interruption', methods=['POST'])
def preview_live_interruption():
    try:
        data = request.get_json(silent=True) or {}
        return jsonify(get_showrunner().maybe_live_interruption(
            data.get('show_draft') or {},
            universe=get_universe(),
            seed=data.get('seed'),
            force=bool(data.get('force', False)),
            autonomy_level=str(data.get('autonomy_level', 'balanced')).lower(),
        ))
    except Exception as exc:
        current_app.logger.exception("Live interruption preview failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/approval-queue/<approval_id>/decision', methods=['POST'])
def decide_booker_approval(approval_id):
    try:
        return jsonify(get_showrunner().decide_approval(approval_id, request.get_json(silent=True) or {}))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 422
    except Exception as exc:
        current_app.logger.exception("Booker approval decision failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/approval-queue/auto-resolve', methods=['POST'])
def auto_resolve_booker_queue():
    try:
        data = request.get_json(silent=True) or {}
        state = get_database().get_game_state() if hasattr(get_database(), 'get_game_state') else {}
        year, week = _request_year_week(data, state)
        return jsonify(get_showrunner().auto_resolve_due(year, week))
    except Exception as exc:
        current_app.logger.exception("Booker queue auto-resolve failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/post-show/fallout/latest', methods=['GET'])
def latest_post_show_fallout():
    try:
        show_id = request.args.get('show_id')
        year = request.args.get('year')
        week = request.args.get('week')
        limit = _coerce_int(request.args.get('limit'), 8)
        return jsonify(get_post_show_fallout().get_latest(
            show_id=show_id,
            year=_coerce_int(year, None) if year not in (None, "") else None,
            week=_coerce_int(week, None) if week not in (None, "") else None,
            limit=limit,
        ))
    except Exception as exc:
        current_app.logger.exception("Post-show fallout latest failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/post-show/fallout/items/<item_id>/decision', methods=['POST'])
def decide_post_show_fallout_item(item_id):
    try:
        return jsonify(get_post_show_fallout().decide_item(item_id, request.get_json(silent=True) or {}))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 422
    except Exception as exc:
        current_app.logger.exception("Post-show fallout decision failed")
        return jsonify({'error': str(exc)}), 500

@booker_bp.route('/api/booker/post-show/fallout/<report_id>/auto-handle', methods=['POST'])
def auto_handle_post_show_fallout(report_id):
    try:
        return jsonify(get_post_show_fallout().auto_handle_report(report_id))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 422
    except Exception as exc:
        current_app.logger.exception("Post-show fallout auto-handle failed")
        return jsonify({'error': str(exc)}), 500


@booker_bp.route('/api/talent-chat/<wrestler_id>/message', methods=['POST'])
def talent_chat_message(wrestler_id):
    data = request.get_json(silent=True) or {}
    state = getattr(get_universe(), 'state', {}) if get_universe() else {}
    year, week = _request_year_week(data, state or {})
    try:
        return jsonify(get_talent_chat_service().chat(wrestler_id, data.get('message', ''), year, week))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'error': f'Talent chat failed: {exc}'}), 500
