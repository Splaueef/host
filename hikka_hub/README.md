# Hikka Hub

Окремий API-сервіс, через який дозволені Hikka можуть бачити одна одну,
обмінюватися подіями й спільними JSON-даними та вести агреговані метрики. Сервіс
не імпортується у Hikka і запускається окремим процесом на заданих IP та порту.

Клієнтом є модуль [`../hikkanet.py`](../hikkanet.py).

## Що вже працює

- heartbeat і стан online/offline для кожної Hikka;
- список зареєстрованих вузлів без публікації секретів або Telegram session;
- шина подій із темами, cursor та автоматичним TTL;
- спільне key/value-сховище JSON із namespace, TTL і revision;
- запис може змінити або видалити лише Hikka, яка створила ключ;
- власні числові метрики та рейтинги між Hikka;
- автоматична статистика запитів, heartbeat, подій і записів;
- локальна CLI для створення, відкликання, повторного ввімкнення й ротації ключів;
- SQLite WAL, очищення прострочених даних, ліміти запитів і розміру body.

## Захист з'єднання

Для кожної Hikka адміністратор випускає окремий набір:

```text
instance_id + key_id + key_secret + Telegram owner_id
```

`key_secret` ніколи не передається API. Кожний запит має HMAC-SHA256-підпис,
який охоплює HTTP-метод, повний шлях із query, SHA-256 body, час, випадковий
nonce, instance ID та Telegram owner ID. Сервер:

1. звіряє ключ із конкретними instance ID та owner ID;
2. перевіряє підпис у constant time;
3. відхиляє застарілий timestamp;
4. зберігає nonce у БД й не дозволяє повторити запит навіть після перезапуску;
5. застосовує окремий rate limit для кожного ключа;
6. дозволяє миттєво відкликати або ротувати один ключ без впливу на інші Hikka.

Важливе обмеження: HTTP-сервер технічно не може довести, що запит створила
саме програма Hikka, а не інша програма з викраденим секретом. Тому доступ
визначається ручним допуском і окремим ключем для кожної перевіреної Hikka.
Не копіюй один ключ на кілька інсталяцій.

HMAC захищає цілісність та автентичність, але не приховує payload. Для доступу
через Інтернет постав сервіс за HTTPS (Nginx/Caddy) або використовуй Tailscale.
Для локальної/VPN-мережі можна підключатися напряму за `http://IP:PORT`.

## Швидкий запуск через Docker

Із каталогу `hikka_hub`:

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8765/health
```

Типово порт публікується лише на `127.0.0.1`. Щоб слухати зовнішній інтерфейс:

```bash
HIKKA_HUB_LISTEN_IP=0.0.0.0 HIKKA_HUB_PUBLIC_PORT=8765 docker compose up -d
```

Не відкривай порт у весь Інтернет без firewall та TLS/VPN.

## Запуск як systemd-сервіс

Приклад очікує, що весь репозиторій розміщено у `/opt/hikka-hub`:

```bash
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin hikkahub
sudo install -d -o hikkahub -g hikkahub -m 700 /var/lib/hikka-hub
cd /opt/hikka-hub
sudo -u hikkahub python3 -m venv .venv
sudo -u hikkahub .venv/bin/pip install -r hikka_hub/requirements.txt
sudo install -m 644 hikka_hub/hikka-hub.service /etc/systemd/system/hikka-hub.service
sudo systemctl daemon-reload
sudo systemctl enable --now hikka-hub
sudo systemctl status hikka-hub
```

Змінити IP/порт можна у `/etc/hikka-hub.env`, наприклад:

```dotenv
HIKKA_HUB_BIND=0.0.0.0
HIKKA_HUB_PORT=8765
HIKKA_HUB_DATABASE=/var/lib/hikka-hub/hikka-hub.sqlite3
```

Після зміни виконай `sudo systemctl restart hikka-hub`.

## Запуск без Docker

Із кореня репозиторію:

```bash
python3 -m venv .venv
.venv/bin/pip install -r hikka_hub/requirements.txt
mkdir -p data
HIKKA_HUB_BIND=0.0.0.0 \
HIKKA_HUB_PORT=8765 \
HIKKA_HUB_DATABASE=./data/hikka-hub.sqlite3 \
.venv/bin/python -m hikka_hub
```

Параметри середовища:

| Змінна | Типове значення | Призначення |
|---|---:|---|
| `HIKKA_HUB_BIND` | `0.0.0.0` | IP, який слухає сервіс |
| `HIKKA_HUB_PORT` | `8765` | TCP-порт сервісу |
| `HIKKA_HUB_DATABASE` | `./data/hikka-hub.sqlite3` | файл SQLite |
| `HIKKA_HUB_MAX_CLOCK_SKEW` | `90` | допустиме відхилення годинника, сек |
| `HIKKA_HUB_RATE_LIMIT` | `120` | запитів на хвилину для одного ключа |
| `HIKKA_HUB_BODY_LIMIT` | `131072` | максимум body у байтах |
| `HIKKA_HUB_EVENT_RETENTION_HOURS` | `168` | максимальний TTL події |
| `HIKKA_HUB_OFFLINE_AFTER` | `120` | коли вузол вважати offline, сек |

## Випуск ключа для окремої Hikka

Telegram ID власника можна переглянути через `.me` або інший системний модуль.
Для Docker команда виконується всередині контейнера, тому БД із named volume
залишається на місці:

```bash
docker compose exec hikka-hub python -m hikka_hub.cli \
  --database /data/hikka-hub.sqlite3 \
  issue \
  --owner-id 123456789 \
  --instance-id hikka-main \
  --name "Main Hikka" \
  --server-url http://10.0.0.5:8765
```

Для звичайного запуску з venv:

```bash
.venv/bin/python -m hikka_hub.cli \
  --database ./data/hikka-hub.sqlite3 \
  issue \
  --owner-id 123456789 \
  --instance-id hikka-main \
  --name "Main Hikka" \
  --server-url http://10.0.0.5:8765
```

Якщо використовується наведений systemd unit, запускай CLI від користувача
сервісу, щоб не змінити власника БД:

```bash
sudo -u hikkahub /opt/hikka-hub/.venv/bin/python -m hikka_hub.cli \
  --database /var/lib/hikka-hub/hikka-hub.sqlite3 \
  issue --owner-id 123456789 --instance-id hikka-main --name "Main Hikka" \
  --server-url https://hub.example.com
```

Секрет показується лише один раз. У самій Hikka рекомендовано відкрити
`.config HikkaNet` і заповнити чотири отримані поля. Швидкий варіант:

```text
.hknetsetup http://10.0.0.5:8765 hikka-main hk_xxxxxxxxxxxx SECRET
```

Модуль одразу видаляє повідомлення з цією командою. Після цього перевір:

```text
.hknetstatus
.hknetnodes
.hknetstats
```

## Керування ключами

```bash
python -m hikka_hub.cli --database ./data/hikka-hub.sqlite3 list
python -m hikka_hub.cli --database ./data/hikka-hub.sqlite3 revoke hikka-main
python -m hikka_hub.cli --database ./data/hikka-hub.sqlite3 enable hikka-main
python -m hikka_hub.cli --database ./data/hikka-hub.sqlite3 rotate hikka-main
```

Після `rotate` старий secret одразу перестає працювати.
Для Docker заміни початок команди на
`docker compose exec hikka-hub python -m hikka_hub.cli --database /data/hikka-hub.sqlite3`.

## Команди модуля

| Команда | Дія |
|---|---|
| `.hknet` | inline-панель |
| `.hknetstatus` | heartbeat і перевірка авторизації |
| `.hknetnodes` | список дозволених Hikka та online-стан |
| `.hknetpublish topic payload` | опублікувати подію |
| `.hknetevents [topic]` | прочитати нові події |
| `.hknetput namespace key value` | записати JSON або текст |
| `.hknetget namespace [key]` | прочитати ключ або namespace |
| `.hknetdel namespace key` | видалити власний ключ |
| `.hknetinc metric [delta]` | збільшити власну метрику |
| `.hknetstats [metric]` | огляд або рейтинг метрики |

JSON із полями на кшталт `token`, `password`, `secret`, `api_key`, `session`
клієнтський модуль відмовиться публікувати, щоб випадково не винести секрет.

## Використання з інших модулів Hikka

Іншим локальним модулям не потрібно бачити `key_secret` або повторювати HMAC.
Вони можуть знайти модуль-конектор та використати його публічний API:

```python
hub = self.lookup("HikkaNet")
if hub is None:
    raise RuntimeError("Спочатку встанови HikkaNet")

await hub.api_publish("deploy", {"version": "2.1.0"})
release = await hub.api_get("releases", "stable")
await hub.api_put("status", "worker-1", {"ready": True}, ttl_seconds=300)
await hub.api_increment("jobs.completed")
events = await hub.api_events(after_id=0, topic="deploy", limit=50)
stats = await hub.api_stats("jobs.completed")
nodes = await hub.api_instances()
```

Доступні методи: `api_publish`, `api_events`, `api_instances`, `api_get`,
`api_put`, `api_delete`, `api_increment` та `api_stats`. Вони проходять ту саму
валідацію, перевірку чутливих полів і захищений підпис, що й команди модуля.

## API v1

Усі `/v1/*` маршрути потребують підпису. Публічними є тільки `/` і `/health`.

| Метод і маршрут | Призначення |
|---|---|
| `GET /v1/me` | перевірити поточний ключ |
| `POST /v1/heartbeat` | оновити присутність та capabilities |
| `GET /v1/instances` | список вузлів |
| `POST /v1/events` | опублікувати подію |
| `GET /v1/events` | прочитати події після cursor |
| `PUT/GET/DELETE /v1/kv/{namespace}/{key}` | спільний запис |
| `GET /v1/kv/{namespace}` | список записів namespace |
| `POST /v1/metrics/{metric}/increment` | збільшити метрику |
| `GET /v1/stats/{metric}` | рейтинг метрики |
| `GET /v1/stats` | загальна статистика сервісу |
