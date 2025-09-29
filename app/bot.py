import asyncio
from datetime import datetime, time, timedelta, date
import zoneinfo
from typing import Dict

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)

from app.config import (
    DEFAULT_TZ, initial_targets, minutes_to_hhmm, hhmm_to_minutes,
    MORNING_HH, MORNING_MM, EVENING_HH, EVENING_MM,
    WAKE_TOL_MIN, SLEEP_TOL_MIN, PTS_OK, PTS_FAIL, PTS_ALL_OK_BONUS,
    DURATION_DAYS, READING_END, FOCUS_END, SCREEN_END, TG_END, WAKE_END, SLEEP_END
)
from app import db as store
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
TZ = zoneinfo.ZoneInfo(DEFAULT_TZ)

def clamp_targets(u):
    # ограничиваем по финальным целям (не выходим за пределы)
    u_read = min(u["reading_target"], READING_END)
    u_focus = min(u["focus_target"], FOCUS_END)
    u_screen = max(u["screen_target"], SCREEN_END)
    u_tg = max(min(u["tg_target"], u_screen), TG_END)  # tg <= screen, и >= финального
    # время: идём к целевому не позже -> минимумы
    u_wake = max(u["wake_target"], hhmm_to_minutes(WAKE_END))    # нельзя стать "раньше" цели (числом меньше), поэтому max с целевым?
    # Время "раньше" это меньше минут => цель конечная = 07:00 (420). Нам нужно min(value, target) чтобы не уйти дальше. Но мы храним только "не позже". Оставим безопасно:
    u_wake = max(u_wake, hhmm_to_minutes(WAKE_END))
    u_sleep = max(u["sleep_target"], hhmm_to_minutes(SLEEP_END))
    return u_read, u_focus, u_screen, u_tg, u_wake, u_sleep

async def schedule_for_user(app, chat_id: int, tz: zoneinfo.ZoneInfo):
    # ежедневные джобы
    app.job_queue.run_daily(morning_job, time=time(MORNING_HH, MORNING_MM, tzinfo=tz), data={"chat_id": chat_id}, name=f"morning-{chat_id}")
    app.job_queue.run_daily(evening_job, time=time(EVENING_HH, EVENING_MM, tzinfo=tz), data={"chat_id": chat_id}, name=f"evening-{chat_id}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now(TZ).date().isoformat()

    t = initial_targets()
    store.upsert_user(
        chat_id, DEFAULT_TZ,
        targets={"reading": t.reading, "focus": t.focus, "screen": t.screen, "tg": t.tg,
                 "wake": t.wake_min, "sleep": t.sleep_min},
        deltas={"reading": t.d_reading, "focus": t.d_focus, "screen": t.d_screen, "tg": t.d_tg,
                "wake": t.d_wake, "sleep": t.d_sleep},
        start_date=now
    )

    await schedule_for_user(context.application, chat_id, TZ)
    await update.message.reply_text(
        "Запущено! 60-дневная лестница активна.\n"
        "Я буду писать каждое утро в 07:00 цели на день и в 22:50 — вечерний опрос.\n"
        "Команды: /goals, /stats, /stop"
    )
    # сразу показать сегодняшние цели если ещё не 07:00
    await send_goals(chat_id, context)

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    store.set_active(chat_id, 0)
    await update.message.reply_text("Остановил. Можешь снова /start когда будешь готов.")

async def goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_goals(update.effective_chat.id, context)

async def send_goals(chat_id: int, context: ContextTypes.DEFAULT_TYPE, with_week=False):
    u = store.get_user(chat_id)
    if not u or not u["active"]:
        return
    # поджать цели в разумные границы
    r, f, s, tg, w, sl = clamp_targets(u)
    # текст
    msg = [
        "Доброе утро!!!",
        f"Сегодняшние цели:",
        f"• Чтение: ≥ {int(round(r))} мин",
        f"• Глубокий фокус: ≥ {int(round(f))} мин",
        f"• Экранное время: ≤ {int(round(s))} мин",
        f"• Telegram: ≤ {int(round(tg))} мин (всегда ≤ экрана)",
        f"• Подъём: не позже {minutes_to_hhmm(int(round(w)))}",
        f"• Сон: не позже {minutes_to_hhmm(int(round(sl)))}",
    ]
    # еженедельная сводка (каждые 7 дней после старта)
    if with_week:
        sd = date.fromisoformat(u["start_date"])
        today = datetime.now(TZ).date()
        days = (today - sd).days
        if days >= 7 and days % 7 == 0:
            d_from = (today - timedelta(days=7)).isoformat()
            d_to = (today - timedelta(days=1)).isoformat()
            st = store.get_week_stats(chat_id, d_from, d_to)
            if st and st.get("days", 0) > 0:
                avg_read = round((st["sum_reading"] or 0) / st["days"])
                avg_focus = round((st["sum_focus"] or 0) / st["days"])
                avg_screen = round((st["sum_screen"] or 0) / st["days"])
                avg_tg = round((st["sum_tg"] or 0) / st["days"])
                avg_wake = minutes_to_hhmm(int(round(st["avg_wake"] or 0)))
                avg_sleep = minutes_to_hhmm(int(round(st["avg_sleep"] or 0)))
                msg += [
                    "",
                    "Итоги за неделю:",
                    f"• Чтение: сумм. {st['sum_reading'] or 0} мин (ср. {avg_read}/д)",
                    f"• Глубокий фокус: сумм. {st['sum_focus'] or 0} мин (ср. {avg_focus}/д)",
                    f"• Экран: сумм. {st['sum_screen'] or 0} мин (ср. {avg_screen}/д)",
                    f"• Telegram: сумм. {st['sum_tg'] or 0} мин (ср. {avg_tg}/д)",
                    f"• Подъём (ср.): {avg_wake}",
                    f"• Сон (ср.): {avg_sleep}",
                ]
    await context.bot.send_message(chat_id=chat_id, text="\n".join(msg))

async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    await send_goals(chat_id, context, with_week=True)

async def evening_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    today = datetime.now(TZ).date().isoformat()
    store.put_survey_state(chat_id, step=0, d=today)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "Вечерний опрос:\n"
            "1) Сколько минут ты сегодня читал? (целое число)"
        )
    )

def success_flags(u, done) -> Dict[str, bool]:
    ok_reading = (done["reading_done"] >= int(round(u["reading_target"])))
    ok_focus   = (done["focus_done"]   >= int(round(u["focus_target"])))
    ok_screen  = (done["screen_done"]  <= int(round(u["screen_target"])))
    ok_tg      = (done["tg_done"]      <= int(round(min(u["tg_target"], u["screen_target"]))))
    # время: успех если woke <= target + допуск
    ok_wake    = (done["wake_actual"]  <= int(round(u["wake_target"])) + WAKE_TOL_MIN)
    ok_sleep   = (done["sleep_actual"] <= int(round(u["sleep_target"])) + SLEEP_TOL_MIN)
    return {
        "reading": ok_reading, "focus": ok_focus, "screen": ok_screen,
        "tg": ok_tg, "wake": ok_wake, "sleep": ok_sleep
    }

async def finalize_day(chat_id: int, d: str, context: ContextTypes.DEFAULT_TYPE):
    s = store.get_survey_state(chat_id)
    u = store.get_user(chat_id)
    if not s or not u: 
        return
    # валидации: tg <= screen уже проверяли при вводе
    done = {
        "reading_done": s["tmp_reading"] or 0,
        "focus_done": s["tmp_focus"] or 0,
        "screen_done": s["tmp_screen"] or 0,
        "tg_done": s["tmp_tg"] or 0,
        "wake_actual": s["tmp_wake"] or 24*60,
        "sleep_actual": s["tmp_sleep"] or 24*60,
    }

    inc = success_flags(u, done)
    any_fail = not all(inc.values())
    # очки
    delta = (sum(1 for v in inc.values() if v) * PTS_OK) + (PTS_ALL_OK_BONUS if all(inc.values()) else 0)
    delta += (sum(1 for v in inc.values() if not v) * PTS_FAIL)

    pts, streak = store.add_points_and_streak(chat_id, delta, success_all=all(inc.values()), any_fail=any_fail)

    # лог + обновление целей/дня
    store.upsert_log(chat_id, d, **done,
                     ok_reading=int(inc["reading"]), ok_focus=int(inc["focus"]),
                     ok_screen=int(inc["screen"]), ok_tg=int(inc["tg"]),
                     ok_wake=int(inc["wake"]), ok_sleep=int(inc["sleep"]))
    store.update_targets_after_day(chat_id, inc)
    store.clear_survey(chat_id)

    # ответ
    lines = ["Сохранено!"]
    for k, ok in inc.items():
        pretty = {
            "reading":"Чтение", "focus":"Глубокий фокус", "screen":"Экран", "tg":"Telegram",
            "wake":"Подъём", "sleep":"Сон"
        }[k]
        mark = "✅" if ok else "❌"
        lines.append(f"{mark} {pretty}")
    lines += [
        "",
        f"Очки за сегодня: {delta:+d}",
        f"Твои очки: {pts}, Стрик: {streak}"
    ]
    # микро-поощрение/наказание (текстом, без насилия)
    if all(inc.values()):
        lines.append("Награда: +50 бонуса за идеальный день. Красавчик!")
    elif any_fail:
        lines.append("Штраф: цели не растут по пунктам с ❌. Завтра попробуем снова.")
    await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    s = store.get_survey_state(chat_id)
    if not s:
        return  # игнорим обычные тексты вне опроса

    text = (update.message.text or "").strip()
    step = s["step"]
    today = s["d"]

    # helper: парсинг int и HH:MM
    def parse_int(msg, low=0, high=10000):
        try:
            v = int(msg)
            if v < low or v > high: return None
            return v
        except:
            return None

    if step == 0:
        v = parse_int(text, 0, 10000)
        if v is None:
            await update.message.reply_text("Введи целое число минут чтения.")
            return
        store.set_survey_value(chat_id, "tmp_reading", v, 1)
        await update.message.reply_text("2) Сколько минут глубокого фокуса сегодня?")
        return

    if step == 1:
        v = parse_int(text, 0, 10000)
        if v is None:
            await update.message.reply_text("Введи целое число минут фокуса.")
            return
        store.set_survey_value(chat_id, "tmp_focus", v, 2)
        await update.message.reply_text("3) Сколько минут всего экранного времени сегодня?")
        return

    if step == 2:
        v = parse_int(text, 0, 1440)
        if v is None:
            await update.message.reply_text("Введи минуты экрана (0–1440).")
            return
        store.set_survey_value(chat_id, "tmp_screen", v, 3)
        await update.message.reply_text("4) Сколько минут в Telegram? (не больше экрана)")
        return

    if step == 3:
        v = parse_int(text, 0, 1440)
        if v is None:
            await update.message.reply_text("Введи минуты в Telegram (0–1440).")
            return
        # проверка tg <= screen
        screen = store.get_survey_state(chat_id)["tmp_screen"] or 0
        if v > screen:
            await update.message.reply_text(f"Telegram не может быть больше экрана. Введи число ≤ {screen}.")
            return
        store.set_survey_value(chat_id, "tmp_tg", v, 4)
        await update.message.reply_text("5) Во сколько ты сегодня проснулся? (часы:минуты, 24ч, например 07:15)")
        return

    if step == 4:
        try:
            v = hhmm_to_minutes(text)
        except:
            await update.message.reply_text("Формат времени HH:MM, например 07:15.")
            return
        store.set_survey_value(chat_id, "tmp_wake", v, 5)
        await update.message.reply_text("6) Во сколько сегодня ложишься спать? (последний вопрос, введи прямо перед сном)")
        return

    if step == 5:
        try:
            v = hhmm_to_minutes(text)
        except:
            await update.message.reply_text("Формат времени HH:MM, например 23:05.")
            return
        store.set_survey_value(chat_id, "tmp_sleep", v, 6)
        # финализация
        await finalize_day(chat_id, today, context)
        return

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = store.get_user(chat_id)
    if not u:
        await update.message.reply_text("Сначала /start.")
        return
    txt = (
        f"День лестницы: {u['day_index']}/{DURATION_DAYS}\n"
        f"Очки: {u['points']} | Стрик: {u['streak']}\n\n"
        f"Текущие цели (с допусками по времени):\n"
        f"Чтение ≥ {int(round(u['reading_target']))} мин\n"
        f"Фокус ≥ {int(round(u['focus_target']))} мин\n"
        f"Экран ≤ {int(round(u['screen_target']))} мин\n"
        f"Telegram ≤ {int(round(min(u['tg_target'], u['screen_target'])))} мин\n"
        f"Подъём не позже {minutes_to_hhmm(int(round(u['wake_target'])))} (+{WAKE_TOL_MIN} мин)\n"
        f"Сон не позже {minutes_to_hhmm(int(round(u['sleep_target'])))} (+{SLEEP_TOL_MIN} мин)\n"
    )
    await update.message.reply_text(txt)

def main():
    store.init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("goals", goals))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))

    # рескейджул джобы для активных юзеров после рестарта
    for u in store.all_active_users():
        app.job_queue.run_daily(morning_job, time=time(MORNING_HH, MORNING_MM, tzinfo=TZ), data={"chat_id": u["chat_id"]}, name=f"morning-{u['chat_id']}")
        app.job_queue.run_daily(evening_job, time=time(EVENING_HH, EVENING_MM, tzinfo=TZ), data={"chat_id": u["chat_id"]}, name=f"evening-{u['chat_id']}")

    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
