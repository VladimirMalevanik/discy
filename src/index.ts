export interface Env {
  KV: KVNamespace;
  BOT_TOKEN: string;
  WEBHOOK_SECRET: string;
  TZ?: string;
  TZ_OFFSET_MIN?: string | number;
}

type Targets = {
  reading: number; focus: number; screen: number; tg: number; wake: number; sleep: number;
};
type Deltas = {
  reading: number; focus: number; screen: number; tg: number; wake: number; sleep: number;
};
type Survey = {
  date: string | null;
  step: number | null;
  tmp: Partial<{
    reading: number; focus: number; screen: number; tg: number; wake: number; sleep: number;
  }>;
};
type User = {
  chatId: number;
  active: boolean;
  startDate: string;
  dayIndex: number;
  points: number;
  streak: number;
  targets: Targets;
  deltas: Deltas;
  survey: Survey;
};

const DAYS = 60;
const READING_START = 20, READING_END = 90;
const FOCUS_START = 30, FOCUS_END = 180;
const SCREEN_START = 180, SCREEN_END = 60;
const TG_START = 90, TG_END = 30;
const WAKE_START = "08:30", WAKE_END = "07:00";
const SLEEP_START = "00:30", SLEEP_END = "23:00";
const WAKE_TOL_MIN = 15, SLEEP_TOL_MIN = 15;
const PTS_OK = 10, PTS_FAIL = -5, PTS_BONUS = 50;

function hhmmToMin(s: string): number {
  const [h, m] = s.split(":").map(Number);
  return h * 60 + m;
}
function minToHHMM(x: number): string {
  const m = ((x % 1440) + 1440) % 1440;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h.toString().padStart(2, "0")}:${mm.toString().padStart(2, "0")}`;
}
function todayLocal(env: Env): string {
  const off = Number(env.TZ_OFFSET_MIN ?? 0);
  const now = new Date(Date.now() + off * 60_000);
  return now.toISOString().slice(0, 10);
}
function nowLocal(env: Env): Date {
  const off = Number(env.TZ_OFFSET_MIN ?? 0);
  return new Date(Date.now() + off * 60_000);
}

function defaultUser(chatId: number): User {
  const dReading = (READING_END - READING_START) / DAYS;
  const dFocus   = (FOCUS_END   - FOCUS_START)   / DAYS;
  const dScreen  = (SCREEN_END  - SCREEN_START)  / DAYS;
  const dTg      = (TG_END      - TG_START)      / DAYS;
  const dWake    = (hhmmToMin(WAKE_END)  - hhmmToMin(WAKE_START))  / DAYS;
  const dSleep   = (hhmmToMin(SLEEP_END) - hhmmToMin(SLEEP_START)) / DAYS;

  return {
    chatId,
    active: true,
    startDate: todayLocal(globalEnv!),
    dayIndex: 0,
    points: 0,
    streak: 0,
    targets: {
      reading: READING_START,
      focus: FOCUS_START,
      screen: SCREEN_START,
      tg: TG_START,
      wake: hhmmToMin(WAKE_START),
      sleep: hhmmToMin(SLEEP_START),
    },
    deltas: { reading: dReading, focus: dFocus, screen: dScreen, tg: dTg, wake: dWake, sleep: dSleep },
    survey: { date: null, step: null, tmp: {} },
  };
}

async function getUser(env: Env, chatId: number): Promise<User> {
  const raw = await env.KV.get(`u:${chatId}`);
  if (!raw) return defaultUser(chatId);
  return JSON.parse(raw) as User;
}
async function putUser(env: Env, u: User): Promise<void> {
  await env.KV.put(`u:${u.chatId}`, JSON.stringify(u));
}
async function addUserToList(env: Env, chatId: number) {
  const raw = await env.KV.get("users");
  const arr = raw ? (JSON.parse(raw) as number[]) : [];
  if (!arr.includes(chatId)) {
    arr.push(chatId);
    await env.KV.put("users", JSON.stringify(arr));
  }
}
async function listUsers(env: Env): Promise<number[]> {
  const raw = await env.KV.get("users");
  return raw ? (JSON.parse(raw) as number[]) : [];
}

function clampTargets(u: User) {
  const t = u.targets;
  t.reading = Math.min(t.reading, READING_END);
  t.focus   = Math.min(t.focus,   FOCUS_END);
  t.screen  = Math.max(t.screen,  SCREEN_END);
  t.tg      = Math.max(Math.min(t.tg, t.screen), TG_END);
  t.wake    = Math.max(t.wake, hhmmToMin(WAKE_END));
  t.sleep   = Math.max(t.sleep, hhmmToMin(SLEEP_END));
}

async function sendMessage(env: Env, chatId: number, text: string) {
  const url = `https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`;
  await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
}

function successFlags(u: User, done: Targets): Record<string, boolean> {
  const t = u.targets;
  const okReading = done.reading >= Math.round(t.reading);
  const okFocus   = done.focus   >= Math.round(t.focus);
  const okScreen  = done.screen  <= Math.round(t.screen);
  const okTg      = done.tg      <= Math.round(Math.min(t.tg, t.screen));
  const okWake    = done.wake    <= Math.round(t.wake)  + WAKE_TOL_MIN;
  const okSleep   = done.sleep   <= Math.round(t.sleep) + SLEEP_TOL_MIN;
  return { reading: okReading, focus: okFocus, screen: okScreen, tg: okTg, wake: okWake, sleep: okSleep };
}

function applyProgress(u: User, inc: Record<string, boolean>) {
  const d = u.deltas, t = u.targets;
  (["reading","focus","screen","tg","wake","sleep"] as (keyof Targets)[]).forEach(k => {
    if (inc[k]) (t as any)[k] += (d as any)[k];
  });
  if (t.tg > t.screen) t.tg = t.screen;
  u.dayIndex = Math.min(DAYS, u.dayIndex + 1);
}

async function morningForUser(env: Env, u: User, withWeek: boolean) {
  clampTargets(u);
  const t = u.targets;
  const lines = [
    "Доброе утро!!!",
    "Сегодняшние цели:",
    `• Чтение: ≥ ${Math.round(t.reading)} мин`,
    `• Глубокий фокус: ≥ ${Math.round(t.focus)} мин`,
    `• Экранное время: ≤ ${Math.round(t.screen)} мин`,
    `• Telegram: ≤ ${Math.round(Math.min(t.tg, t.screen))} мин (всегда ≤ экрана)`,
    `• Подъём: не позже ${minToHHMM(Math.round(t.wake))}`,
    `• Сон: не позже ${minToHHMM(Math.round(t.sleep))}`,
  ];
  if (withWeek) {
    const sd = new Date(u.startDate);
    const days = Math.floor((nowLocal(env).getTime() - sd.getTime()) / 86_400_000);
    if (days >= 7 && days % 7 === 0) {
      lines.push("", `Итоги: закончилась неделя. День лестницы: ${u.dayIndex}/${DAYS}`);
    }
  }
  await sendMessage(env, u.chatId, lines.join("\n"));
}

async function eveningStart(env: Env, u: User) {
  u.survey = { date: todayLocal(env), step: 0, tmp: {} };
  await putUser(env, u);
  await sendMessage(env, u.chatId, "Вечерний опрос:\n1) Сколько минут ты сегодня читал? (целое число)");
}

async function finalizeDay(env: Env, u: User) {
  const tmp = u.survey.tmp;
  const done: Targets = {
    reading: tmp.reading ?? 0,
    focus:   tmp.focus   ?? 0,
    screen:  tmp.screen  ?? 0,
    tg:      tmp.tg      ?? 0,
    wake:    tmp.wake    ?? 24*60,
    sleep:   tmp.sleep   ?? 24*60,
  };
  const inc = successFlags(u, done);
  const allOk = Object.values(inc).every(Boolean);
  const delta = Object.values(inc).filter(Boolean).length * PTS_OK
              + (allOk ? PTS_BONUS : 0)
              + Object.values(inc).filter(v => !v).length * PTS_FAIL;

  u.points = Math.max(0, u.points + delta);
  u.streak = allOk ? u.streak + 1 : 0;
  applyProgress(u, inc);
  u.survey = { date: null, step: null, tmp: {} };
  await putUser(env, u);

  const pretty: Record<string,string> = {
    reading:"Чтение", focus:"Глубокий фокус", screen:"Экран", tg:"Telegram", wake:"Подъём", sleep:"Сон"
  };
  const lines = ["Сохранено!"];
  for (const k of Object.keys(inc)) {
    lines.push(`${inc[k] ? "✅" : "❌"} ${pretty[k]}`);
  }
  lines.push("", `Очки за сегодня: ${delta >= 0 ? "+"+delta : String(delta)}`,
                  `Всего очков: ${u.points}, Стрик: ${u.streak}`);
  if (allOk) lines.push("Награда: +50 бонуса за идеальный день. Красавчик!");
  else lines.push("Штраф: цели по ❌ не растут. Завтра снова пробуем.");
  await sendMessage(env, u.chatId, lines.join("\n"));
}

let globalEnv: Env | null = null;

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    globalEnv = env;
    const url = new URL(req.url);
    if (req.method === "POST" && url.pathname === `/webhook/${env.WEBHOOK_SECRET}`) {
      const update = await req.json<any>();
      const msg = update.message ?? update.edited_message ?? null;
      if (!msg || !msg.chat) return new Response("ok");
      const chatId = msg.chat.id as number;
      await addUserToList(env, chatId);
      let u = await getUser(env, chatId);

      const text: string = (msg.text || "").trim();

      // /start — инициализация
      if (text === "/start") {
        u.active = true;
        if (!u.startDate) u.startDate = todayLocal(env);
        await putUser(env, u);
        await sendMessage(env, chatId, "Запущено! Я буду писать в 07:00 цели и в 22:50 — опрос.\nКоманды: /start, /stats, /stop");
        await morningForUser(env, u, false);
        return new Response("ok");
      }

      if (text === "/stop") {
        u.active = false;
        await putUser(env, u);
        await sendMessage(env, chatId, "Остановил. Возврат — /start");
        return new Response("ok");
      }

      if (text === "/stats") {
        const t = u.targets;
        const ans =
`День лестницы: ${u.dayIndex}/${DAYS}
Очки: ${u.points} | Стрик: ${u.streak}

Текущие цели:
Чтение ≥ ${Math.round(t.reading)} мин
Фокус ≥ ${Math.round(t.focus)} мин
Экран ≤ ${Math.round(t.screen)} мин
Telegram ≤ ${Math.round(Math.min(t.tg, t.screen))} мин
Подъём не позже ${minToHHMM(Math.round(t.wake))} (+${WAKE_TOL_MIN} мин)
Сон не позже ${minToHHMM(Math.round(t.sleep))} (+${SLEEP_TOL_MIN} мин)`;
        await sendMessage(env, chatId, ans);
        return new Response("ok");
      }

      // обработка вечернего опроса
      if (u.survey.step === null) {
        // игнор текст вне опроса
        return new Response("ok");
      }

      const step = u.survey.step!;
      const n = (s: string) => /^\d+$/.test(s) ? parseInt(s, 10) : NaN;
      const time2min = (s: string) => /^\d{2}:\d{2}$/.test(s) ? hhmmToMin(s) : NaN;

      if (step === 0) {
        const v = n(text);
        if (Number.isNaN(v)) { await sendMessage(env, chatId, "Введи целое число минут чтения."); return new Response("ok"); }
        u.survey.tmp.reading = v; u.survey.step = 1; await putUser(env, u);
        await sendMessage(env, chatId, "2) Сколько минут глубокого фокуса?");
        return new Response("ok");
      }
      if (step === 1) {
        const v = n(text);
        if (Number.isNaN(v)) { await sendMessage(env, chatId, "Введи целое число минут фокуса."); return new Response("ok"); }
        u.survey.tmp.focus = v; u.survey.step = 2; await putUser(env, u);
        await sendMessage(env, chatId, "3) Сколько минут всего экранного времени?");
        return new Response("ok");
      }
      if (step === 2) {
        const v = n(text);
        if (Number.isNaN(v) || v<0 || v>1440) { await sendMessage(env, chatId, "Введи минуты экрана (0–1440)."); return new Response("ok"); }
        u.survey.tmp.screen = v; u.survey.step = 3; await putUser(env, u);
        await sendMessage(env, chatId, "4) Сколько минут в Telegram? (не больше экрана)");
        return new Response("ok");
      }
      if (step === 3) {
        const v = n(text);
        if (Number.isNaN(v) || v<0 || v>1440) { await sendMessage(env, chatId, "Введи минуты Telegram (0–1440)."); return new Response("ok"); }
        const screen = u.survey.tmp.screen ?? 0;
        if (v > screen) { await sendMessage(env, chatId, `Telegram не может быть больше экрана. Введи ≤ ${screen}.`); return new Response("ok"); }
        u.survey.tmp.tg = v; u.survey.step = 4; await putUser(env, u);
        await sendMessage(env, chatId, "5) Во сколько ты сегодня проснулся? (HH:MM, например 07:15)");
        return new Response("ok");
      }
      if (step === 4) {
        const v = time2min(text);
        if (Number.isNaN(v)) { await sendMessage(env, chatId, "Формат времени HH:MM, например 07:15."); return new Response("ok"); }
        u.survey.tmp.wake = v; u.survey.step = 5; await putUser(env, u);
        await sendMessage(env, chatId, "6) Во сколько сегодня ложишься спать? (последний вопрос, введи прямо перед сном)");
        return new Response("ok");
      }
      if (step === 5) {
        const v = time2min(text);
        if (Number.isNaN(v)) { await sendMessage(env, chatId, "Формат времени HH:MM, например 23:05."); return new Response("ok"); }
        u.survey.tmp.sleep = v; u.survey.step = null; await putUser(env, u);
        await finalizeDay(env, u);
        return new Response("ok");
      }

      return new Response("ok");
    }

    // простая проверка что воркер жив
    if (req.method === "GET" && new URL(req.url).pathname === "/health") {
      return new Response("ok");
    }

    return new Response("not found", { status: 404 });
  },

  // ДВА КРОНА В UTC (заданы в wrangler.toml)
  async scheduled(_evt: ScheduledEvent, env: Env, _ctx: ExecutionContext): Promise<void> {
    const users = await listUsers(env);
    for (const chatId of users) {
      const u = await getUser(env, chatId);
      if (!u.active) continue;
      // определяем по минуте: если сейчас ровно одна из наших CRON — что именно?
      // так как у нас два cron-запуска, просто проверим время локальное:
      const now = nowLocal(env);
      const hh = now.getUTCHours(); // но у нас crons уже в UTC — не важно, вызывается дважды в нужные моменты
      const mm = now.getUTCMinutes();

      // Разделим по минуте запуска: 05:00 UTC (утро) и 20:50 UTC (вечер) из примера
      // Чтобы не завязываться на значения здесь, делаем логику по состоянию опроса:
      if (u.survey.step === null) {
        // предположим утренний крон
        await morningForUser(env, u, true);
      } else {
        // если незавершён вчерашний опрос, ничего не стартуем
      }
      // второй крон запустит вечерний опрос:
      // Он проверится здесь же, но по простоте — сразу стартуем опрос, если ещё не начат сегодня
      const today = todayLocal(env);
      if (u.survey.date !== today || u.survey.step === null) {
        // старт вечернего опроса
        await eveningStart(env, u);
      }
    }
  }
};
