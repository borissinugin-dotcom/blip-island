# Настройка Cloudflare Worker для Blip (ЮMoney / СБП + Ed25519 + 10 000 кодов)

## 1. Генерация ключей Ed25519 и 10 000 одноразовых кодов
Выполните из корня проекта:
```bat
node cloudflare_worker\scripts\setup_keys_and_codes.mjs
```
В папке `cloudflare_worker\.secrets\` появятся:
- `ed25519_private.jwk.json` — **приватный ключ** (НИКОМУ не показывать, загружается только в секрет Cloudflare).
- `ed25519_public.jwk.json` — **публичный ключ** (вставляется в клиент Blip для оффлайн-проверки `blip.lic`).
- `codes_10000.txt` и `kv_bulk_10000.json` — 10 000 случайных одноразовых кодов.
- `admin_token.txt` — токен для админ-загрузки кодов.

## 2. Деплой в Cloudflare (бесплатный тариф)
```bat
cd cloudflare_worker
npx.cmd wrangler login
npx.cmd wrangler kv namespace create BLIP_KV
```
Вставьте полученный `id` в `cloudflare_worker/wrangler.toml` вместо `REPLACE_WITH_YOUR_KV_NAMESPACE_ID`.

Загрузите секреты (из папки `cloudflare_worker`):
```bat
npx.cmd wrangler secret put YOOMONEY_WALLET
npx.cmd wrangler secret put YOOMONEY_SECRET
npx.cmd wrangler secret put ED25519_PRIVATE_JWK
npx.cmd wrangler secret put ADMIN_TOKEN
```
* `YOOMONEY_WALLET` — номер вашего кошелька ЮMoney (`41001...`).
* `YOOMONEY_SECRET` — секретное слово из настроек HTTP-уведомлений ЮMoney.
* `ED25519_PRIVATE_JWK` — содержимое файла `.secrets/ed25519_private.jwk.json` (одной строкой).

Загрузите 10 000 кодов в KV и задеплойте Worker:
```bat
npx.cmd wrangler kv bulk put .secrets/kv_bulk_10000.json --binding=BLIP_KV
npx.cmd wrangler deploy
```

## 3. Подключение ЮMoney HTTP-уведомлений
В настройках кошелька ЮMoney (**Настройки -> Пакеты / HTTP-уведомления**):
- Укажите URL: `https://<ваш-воркер>.workers.dev/yoomoney/webhook`
- Скопируйте секрет уведомлений в `YOOMONEY_SECRET`.
- Для перехода с тестовой суммы (10 ₽) на боевую (300 ₽) поменяйте `MIN_AMOUNT_RUB = "300"` в `wrangler.toml` и снова выполните `npx.cmd wrangler deploy`.
