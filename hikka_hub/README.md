# Hikka Hub

Окремий API-сервіс, через який дозволені Hikka можуть бачити одна одну,
обмінюватися подіями й спільними JSON-даними та вести агреговані метрики. Сервіс
не імпортується у Hikka і запускається окремим процесом на заданих IP та порту.

Клієнтом є модуль [`../hikkanet.py`](../hikkanet.py).

## Що вже працює

- heartbeat і стан online/offline для кожної Hikka;
- список зареєстрованих вузлів без публікації секретів або Telegram session;
- шина подій із темами, cursor та автоматичним TTL;
- читання останніх подій для історії кімнат;
- спільне key/value-сховище JSON із namespace, TTL і revision;
- запис може змінити або видалити лише Hikka, яка створила ключ;
- власні числові метрики та рейтинги між Hikka;
- атомарне пакетне додавання до 100 метрик за один запит;
- серверні міжчатові партії з відкритим матчмейкінгом і приватними запрошеннями;
- авторитетна перевірка ходів у хрестиках-нуликах, шашках, шахах і ґо 9×9/13×13;
- глобальні wins/losses/draws, окремий Elo для кожної гри та профіль гравця;
- короткожива присутність у HikkaNetChat, список кімнат і online-учасників;
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

## Оновлення з Hikka Hub 1.x

Схема SQLite доповнюється автоматично й наявні ключі, події та метрики не
видаляються. Після отримання нового коду перебудуй і перезапусти сервіс:

```bash
git pull --ff-only
docker compose up -d --build
curl http://127.0.0.1:8765/health
```

У відповіді `/health` має бути версія `2.0.0`. Далі один раз онови в Hikka
`hikkanet.py`, потім `minigames.py`, `hikkanetchat.py` і `modulehub.py`.
Клієнтські модулі показують зрозумілу помилку, якщо сервер або HikkaNet ще старі.

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
| `HIKKA_HUB_GAME_WAIT_TTL` | `3600` | скільки живе неприйнята партія, сек |
| `HIKKA_HUB_GAME_ACTIVE_TTL` | `604800` | TTL активної партії без ходів, сек |
| `HIKKA_HUB_CHAT_PRESENCE_TTL` | `120` | TTL online-присутності в кімнаті, сек |
| `HIKKA_HUB_MODULE_UPDATES` | `true` | сервер перевіряє manifest оновлень |
| `HIKKA_HUB_MODULE_UPDATE_INTERVAL` | `300` | інтервал перевірки manifest, сек |

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
await hub.api_increment_many({"jobs.completed": 2, "jobs.failed": 1})
events = await hub.api_events(after_id=0, topic="deploy", limit=50)
latest = await hub.api_events(topic="chat.lobby", limit=20, latest=True)
stats = await hub.api_stats("jobs.completed")
nodes = await hub.api_instances()

game = await hub.api_game_create("chess", "hikka-friend")
game = await hub.api_game_join(game["game_id"])
game = await hub.api_game_move(
    game["game_id"], game["revision"],
    {"type": "move", "source": 52, "target": 36},
)
leaders = await hub.api_game_leaderboard("chess", sort="rating")

await hub.api_chat_presence("lobby", "Мій нік")
rooms = await hub.api_chat_rooms()
members = await hub.api_chat_members("lobby")
```

Доступні методи: `api_publish`, `api_events`, `api_instances`, `api_get`,
`api_put`, `api_delete`, `api_increment`, `api_increment_many`, `api_stats`,
`api_game_*` та `api_chat_*`.
Вони проходять ту саму валідацію, перевірку чутливих полів і захищений підпис,
що й команди модуля.

Рекомендований шлях для модулів репозиторію — `ModuleHub`: він збирає локальні
лічильники, надсилає їх одним batch-запитом щохвилини та оновлює snapshot
поточного вузла в namespace `module_stats`.

```python
module_hub = self.lookup("ModuleHub")
if module_hub:
    module_hub.report_stat(self, "jobs.completed")

def modulehub_stats(self):
    return {"completed": 42, "queued": 3}  # тільки агрегати, без ID/текстів
```

Окремий `hikkanetchat.py` використовує event-теми `chat.<room>` для повідомлень
і `/v1/chat/*` для короткоживої online-присутності.
Команди: `.hkchat`, `.hkjoin`, `.hkroom`, `.hkleave`, `.hknick`, `.hksay` та
`.hkhistory`, `.hkrooms`, `.hkmembers`.

## HTTP API (`/v1`, можливості сервера v2)

Усі `/v1/*` маршрути потребують підпису. Публічними є тільки `/` і `/health`.

| Метод і маршрут | Призначення |
|---|---|
| `GET /v1/me` | перевірити поточний ключ |
| `POST /v1/heartbeat` | оновити присутність та capabilities |
| `GET /v1/instances` | список вузлів |
| `POST/GET /v1/games` | створити партію або отримати лобі/свої матчі |
| `GET /v1/games/{id}` | актуальний стан доступної партії |
| `POST /v1/games/{id}/join` | атомарно прийняти відкриту гру/запрошення |
| `POST /v1/games/{id}/move` | серверно перевірити хід із `if_revision` |
| `POST /v1/games/{id}/resign` | здатися й один раз записати результат |
| `DELETE /v1/games/{id}` | скасувати очікування або відхилити запрошення |
| `GET /v1/games/leaderboard` | глобальний рейтинг за wins/played/Elo |
| `GET /v1/games/profile` | статистика власника поточного ключа |
| `POST /v1/chat/presence` | heartbeat участі в кімнаті |
| `DELETE /v1/chat/presence/{room}` | вийти з кімнати |
| `GET /v1/chat/rooms` | активні кімнати й активність за 24 години |
| `GET /v1/chat/rooms/{room}/members` | online-учасники кімнати |
| `POST /v1/events` | опублікувати подію |
| `GET /v1/events` | події після cursor або останні з `latest=1` |
| `PUT/GET/DELETE /v1/kv/{namespace}/{key}` | спільний запис |
| `GET /v1/kv/{namespace}` | список записів namespace |
| `POST /v1/metrics/{metric}/increment` | збільшити метрику |
| `POST /v1/metrics/batch` | атомарно збільшити до 100 метрик |
| `GET /v1/stats/{metric}` | рейтинг метрики |
| `GET /v1/stats` | загальна статистика сервісу |
