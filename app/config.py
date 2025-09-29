import os
from dataclasses import dataclass

DURATION_DAYS = 60

DEFAULT_TZ = os.getenv("TZ", "Europe/Amsterdam")

# стартовые и конечные цели (можешь править под себя)
READING_START = 20        # мин/день
READING_END = 90

FOCUS_START = 30          # мин/день
FOCUS_END = 180

SCREEN_START = 180        # мин/день (максимум)
SCREEN_END = 60

TG_START = 90             # мин/день (максимум, всегда <= screen)
TG_END = 30

WAKE_START = "08:30"      # цель "вставать не позже"
WAKE_END = "07:00"

SLEEP_START = "00:30"     # цель "ложиться не позже"
SLEEP_END = "23:00"

MORNING_HH = 7
MORNING_MM = 0
EVENING_HH = 22
EVENING_MM = 50

# допуски по времени (чтобы не душнить до минуты)
WAKE_TOL_MIN = 15     # проснулся не позже цели + 15 мин
SLEEP_TOL_MIN = 15    # лёг спать не позже цели + 15 мин

# геймификация
PTS_OK = 10
PTS_FAIL = -5
PTS_ALL_OK_BONUS = 50


def hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


def minutes_to_hhmm(total: int) -> str:
    total %= 24 * 60
    h = total // 60
    m = total % 60
    return f"{h:02d}:{m:02d}"


@dataclass
class Targets:
    reading: float
    focus: float
    screen: float
    tg: float
    wake_min: float
    sleep_min: float
    d_reading: float
    d_focus: float
    d_screen: float
    d_tg: float
    d_wake: float
    d_sleep: float

def initial_targets():
    wake_start_min = hhmm_to_minutes(WAKE_START)
    wake_end_min = hhmm_to_minutes(WAKE_END)
    sleep_start_min = hhmm_to_minutes(SLEEP_START)
    sleep_end_min = hhmm_to_minutes(SLEEP_END)

    # шаги в день (float), время идёт "вниз" к целям => отрицательные дельты если надо раньше
    d_reading = (READING_END - READING_START) / DURATION_DAYS
    d_focus   = (FOCUS_END - FOCUS_START) / DURATION_DAYS
    d_screen  = (SCREEN_END - SCREEN_START) / DURATION_DAYS
    d_tg      = (TG_END - TG_START) / DURATION_DAYS
    d_wake    = (wake_end_min - wake_start_min) / DURATION_DAYS
    d_sleep   = (sleep_end_min - sleep_start_min) / DURATION_DAYS

    return Targets(
        reading=float(READING_START),
        focus=float(FOCUS_START),
        screen=float(SCREEN_START),
        tg=float(TG_START),
        wake_min=float(wake_start_min),
        sleep_min=float(sleep_start_min),
        d_reading=d_reading,
        d_focus=d_focus,
        d_screen=d_screen,
        d_tg=d_tg,
        d_wake=d_wake,
        d_sleep=d_sleep,
    )
