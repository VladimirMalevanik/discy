import os, sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Optional, Dict, Any, Tuple, List

DB_PATH = os.getenv("DB_PATH", "data/bot.db")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    with db() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
          chat_id INTEGER PRIMARY KEY,
          tz TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 0,
          start_date TEXT,
          day_index INTEGER NOT NULL DEFAULT 0,
          reading_target REAL,
          focus_target REAL,
          screen_target REAL,
          tg_target REAL,
          wake_target REAL,
          sleep_target REAL,
          d_reading REAL, d_focus REAL, d_screen REAL, d_tg REAL, d_wake REAL, d_sleep REAL,
          points INTEGER NOT NULL DEFAULT 0,
          streak  INTEGER NOT NULL DEFAULT 0
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
          chat_id INTEGER NOT NULL,
          d TEXT NOT NULL,  -- YYYY-MM-DD
          reading_done INTEGER,
          focus_done INTEGER,
          screen_done INTEGER,
          tg_done INTEGER,
          wake_actual INTEGER,
          sleep_actual INTEGER,
          ok_reading INTEGER,
          ok_focus INTEGER,
          ok_screen INTEGER,
          ok_tg INTEGER,
          ok_wake INTEGER,
          ok_sleep INTEGER,
          PRIMARY KEY (chat_id, d)
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS survey (
          chat_id INTEGER PRIMARY KEY,
          step INTEGER NOT NULL,
          d TEXT NOT NULL,
          tmp_reading INTEGER,
          tmp_focus INTEGER,
          tmp_screen INTEGER,
          tmp_tg INTEGER,
          tmp_wake INTEGER,
          tmp_sleep INTEGER
        );
        """)

def upsert_user(chat_id: int, tz: str, targets: Dict[str, float], deltas: Dict[str, float], start_date: str):
    with db() as con:
        con.execute("""
        INSERT INTO users (chat_id, tz, active, start_date, day_index,
          reading_target, focus_target, screen_target, tg_target, wake_target, sleep_target,
          d_reading, d_focus, d_screen, d_tg, d_wake, d_sleep
        )
        VALUES (?, ?, 1, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
          active=1, tz=excluded.tz, start_date=excluded.start_date,
          reading_target=excluded.reading_target, focus_target=excluded.focus_target,
          screen_target=excluded.screen_target, tg_target=excluded.tg_target,
          wake_target=excluded.wake_target, sleep_target=excluded.sleep_target,
          d_reading=excluded.d_reading, d_focus=excluded.d_focus,
          d_screen=excluded.d_screen, d_tg=excluded.d_tg,
          d_wake=excluded.d_wake, d_sleep=excluded.d_sleep
        ;
        """, (chat_id, tz, start_date,
              targets["reading"], targets["focus"], targets["screen"], targets["tg"],
              targets["wake"], targets["sleep"],
              deltas["reading"], deltas["focus"], deltas["screen"], deltas["tg"],
              deltas["wake"], deltas["sleep"]))

def get_user(chat_id: int) -> Optional[sqlite3.Row]:
    with db() as con:
        cur = con.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))
        return cur.fetchone()

def all_active_users() -> List[sqlite3.Row]:
    with db() as con:
        cur = con.execute("SELECT * FROM users WHERE active=1")
        return cur.fetchall()

def set_active(chat_id: int, active: int):
    with db() as con:
        con.execute("UPDATE users SET active=? WHERE chat_id=?", (active, chat_id))

def update_targets_after_day(chat_id: int, inc: Dict[str, bool]):
    with db() as con:
        u = con.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        if not u: return
        # если цель выполнена -> двигаем по дельте, иначе оставляем
        def step(val, d, ok, end_is_min=False):
            if ok:
                newv = val + d
                return newv
            return val

        # применяем
        new_reading = step(u["reading_target"], u["d_reading"], inc["reading"])
        new_focus   = step(u["focus_target"],   u["d_focus"],   inc["focus"])
        new_screen  = step(u["screen_target"],  u["d_screen"],  inc["screen"])
        new_tg      = step(u["tg_target"],      u["d_tg"],      inc["tg"])
        new_wake    = step(u["wake_target"],    u["d_wake"],    inc["wake"])
        new_sleep   = step(u["sleep_target"],   u["d_sleep"],   inc["sleep"])

        # гарантии: tg <= screen
        if new_tg > new_screen:
            new_tg = new_screen

        con.execute("""
          UPDATE users SET
            reading_target=?, focus_target=?, screen_target=?, tg_target=?,
            wake_target=?, sleep_target=?,
            day_index=CASE WHEN day_index<60 THEN day_index+1 ELSE day_index END
          WHERE chat_id=?
        """, (new_reading, new_focus, new_screen, new_tg, new_wake, new_sleep, chat_id))

def upsert_log(chat_id: int, d: str, **vals):
    cols = ", ".join(vals.keys())
    placeholders = ", ".join(["?"]*len(vals))
    updates = ", ".join([f"{k}=excluded.{k}" for k in vals.keys()])
    with db() as con:
        con.execute(f"""
        INSERT INTO logs (chat_id, d, {cols})
        VALUES (?, ?, {placeholders})
        ON CONFLICT(chat_id, d) DO UPDATE SET {updates}
        """, (chat_id, d, *vals.values()))

def get_week_stats(chat_id: int, d_from: str, d_to: str) -> Dict[str, Any]:
    with db() as con:
        cur = con.execute("""
        SELECT
          COUNT(*) as days,
          SUM(COALESCE(reading_done,0)) as sum_reading,
          SUM(COALESCE(focus_done,0))   as sum_focus,
          SUM(COALESCE(screen_done,0))  as sum_screen,
          SUM(COALESCE(tg_done,0))      as sum_tg,
          AVG(COALESCE(wake_actual,0))  as avg_wake,
          AVG(COALESCE(sleep_actual,0)) as avg_sleep
        FROM logs
        WHERE chat_id=? AND d BETWEEN ? AND ?;
        """, (chat_id, d_from, d_to)).fetchone()
        return dict(cur) if cur else {}

def put_survey_state(chat_id: int, step: int, d: str):
    with db() as con:
        con.execute("""
        INSERT INTO survey (chat_id, step, d) VALUES (?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET step=excluded.step, d=excluded.d,
            tmp_reading=NULL, tmp_focus=NULL, tmp_screen=NULL, tmp_tg=NULL, tmp_wake=NULL, tmp_sleep=NULL
        """, (chat_id, step, d))

def get_survey_state(chat_id: int) -> Optional[sqlite3.Row]:
    with db() as con:
        return con.execute("SELECT * FROM survey WHERE chat_id=?", (chat_id,)).fetchone()

def set_survey_value(chat_id: int, field: str, value: int, next_step: int):
    with db() as con:
        con.execute(f"UPDATE survey SET {field}=?, step=? WHERE chat_id=?", (value, next_step, chat_id))

def clear_survey(chat_id: int):
    with db() as con:
        con.execute("DELETE FROM survey WHERE chat_id=?", (chat_id,))

def add_points_and_streak(chat_id: int, delta_pts: int, success_all: bool, any_fail: bool):
    with db() as con:
        u = con.execute("SELECT points, streak FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        if not u: return
        pts = max(0, u["points"] + delta_pts + (50 if success_all else 0))
        streak = u["streak"] + 1 if not any_fail else 0
        con.execute("UPDATE users SET points=?, streak=? WHERE chat_id=?", (pts, streak, chat_id))
        return pts, streak
