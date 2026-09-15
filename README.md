# 🎮 Brawl Stars Account Sale Bot

Telegram-бот для продажи аккаунтов Brawl Stars с автоматической оценкой через API.

## Установка

```bash
pip install -r requirements.txt
```

## Настройка

1. Скопируй `.env.example` в `.env`:
```bash
cp .env.example .env
```

2. Заполни `.env`:
```
BOT_TOKEN=токен от @BotFather
BRAWL_API_KEY=ключ с https://developer.brawlstars.com
ADMIN_ID=твой Telegram ID (узнать: @userinfobot)
```

## Запуск

```bash
python bot.py
```

## Флоу бота

1. `/start` → приветствие + выбор игры
2. Ввод тега → запрос к Brawl Stars API
3. Показ статистики + автоматическая оценка
4. Выбор способа оплаты (Карта / ЮMoney / TON / Fragment)
5. Ввод реквизитов
6. Инструкция по передаче аккаунта
7. Кнопка «Аккаунт передан»
8. Отправка видео → заявка летит тебе в ТГ

## Формула оценки

```
цена = кубки × 0.05 + бравлеры_макс × 30 + всего_бравлеров × 10
```
Минимум 100 ₽. Можешь подправить в функции `calculate_price()`.

## Подключение платёжных систем

В коде уже есть кнопки выбора:
- 💛 **ЮMoney** — подключи через [ЮKassa API](https://yookassa.ru/developers)
- 💎 **TON** — через [@wallet](https://t.me/wallet) или TonConnect
- ⚡️ **Fragment** — через Fragment API

Заглушки находятся в хендлере `payment_method_chosen` — замени на реальные вызовы API.
