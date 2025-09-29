# Discipline Bot — Cloudflare Workers (FREE 24/7)

## Что это
Телеграм-бот на Cloudflare Workers:
- Вебхук Telegram — всегда онлайн (free).
- Два CRON-триггера в сутки: 07:00 и 22:50 **по твоей зоне** (в crons — по UTC).
- Состояние в Cloudflare KV (free).
- Всё хранится и собирается из GitHub.

## Быстрый запуск (шаги)
1) Установи Wrangler:
```bash
npm i -g wrangler
