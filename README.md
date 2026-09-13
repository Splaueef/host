# Host

This repository contains files used for hosting services and resources related to Hikka-based projects.

⚠️ This is **not an official repository** of the original Hikka project.

This repository is a **mirror / convenience copy** created to keep hosting resources available and functional.  
The original repository was copied manually and uploaded as a separate project so the resources can continue working even if the original infrastructure becomes unavailable.

The purpose of this repository is to maintain hosting-related files used by custom builds, forks, and related tools.

## ModuleHub

`modulehub.py` додає єдиний owner-only центр керування модулями через inline-бота. Він автоматично показує актуальні команди завантажених модулів і підтримує:

- категорії, пошук та пагінацію команд;
- запуск команд з аргументами або у відповідь на повідомлення;
- каталог модулів `Splaueef/host` зі встановленням та оновленням;
- встановлення за короткою назвою чи перевіреним HTTPS-посиланням на `.py`;
- налаштування всіх зовнішніх і системних модулів Hikka;
- перемикачі Boolean, перевірку значень штатними валідаторами й скидання до типового;
- повне приховування API-ключів та інших `Hidden`-параметрів;
- підтвердження встановлення, видалення й інших небезпечних дій;
- локальну інтеграційну шину для інших модулів;
- автоматичний облік запусків команд усіх завантажених модулів;
- пакетну синхронізацію агрегованих метрик і коротких snapshot у HikkaNet.

`ModuleHub` не відправляє текст Telegram-команд, аргументи, повідомлення,
chat/user ID, токени чи session. У HikkaNet ідуть лише назва модуля, назва
лічильника, числове значення та безпечні агрегати, які модуль явно повернув із
`modulehub_stats()`.

### Автоматичне оновлення модулів

`ModuleHub` звіряє встановлені модулі з HTTPS-manifest
`module_versions.json`: перша перевірка виконується приблизно через 90 секунд
після запуску Hikka, наступні — типово кожні 6 годин. Якщо у каталозі є новіша
версія, ModuleHub послідовно запускає штатний Loader для її встановлення.

- оновлюються лише вже встановлені модулі з каталогу `Splaueef/host`;
- відсутні модулі автоматично не встановлюються;
- старіша версія з репозиторію не замінює новішу локальну;
- результат або помилка надходить у «Збережені повідомлення»;
- кнопка `♻️ Автооновлення` у `.modmenu` запускає перевірку вручну;
- `auto_update_exclude` у `.config ModuleHub` виключає вибрані модулі;
- `auto_update_interval` змінює інтервал, а `auto_update_notify` — сповіщення.

Сам `ModuleHub` оновлюється вручну через `.dlmod`, щоб він не вивантажував
власний процес посеред перевірки інших модулів.

Відкрити панель: `.modmenu`.

Встановити окремо:

```text
.dlmod https://raw.githubusercontent.com/Splaueef/host/main/modulehub.py
```

## HikkaNet та окремий Hikka Hub сервіс

`hikkanet.py` підключає Hikka до окремого API-процесу з каталогу
`hikka_hub/`. Сервіс слухає задані IP та порт і дає авторизованим Hikka
heartbeat, список online-вузлів, обмін подіями й JSON-даними, власні метрики та
загальну статистику. Hikka Hub 2.0 також тримає авторитетний стан глобальних
партій, перевіряє кожен хід і веде спільний рейтинг гравців. Підтримуються
хрестики-нулики, шашки, шахи та ґо 9×9/13×13; відкриту партію можна знайти з
будь-якого чату, а приватну — адресувати конкретному `instance_id`.

Кожна інсталяція має окремі `instance_id`, `key_id` і `key_secret`, прив'язані
до Telegram ID її власника. Усі API-запити підписуються HMAC-SHA256 і мають
timestamp та одноразовий nonce; ключ однієї Hikka можна відкликати або ротувати
без відключення інших. Повна інструкція Docker, systemd, видачі ключів і API:
[`hikka_hub/README.md`](hikka_hub/README.md).

```text
.dlmod https://raw.githubusercontent.com/Splaueef/host/main/hikkanet.py
.hknet
```

### HikkaNetChat

`hikkanetchat.py` — окремий модуль кімнат для спілкування між Hikka, яким
адміністратор Hikka Hub видав індивідуальні ключі. Він використовує готове
HMAC-підключення модуля `HikkaNet`, тому не бачить і не копіює `key_secret`.

```text
.dlmod https://raw.githubusercontent.com/Splaueef/host/main/hikkanetchat.py
.hkchat
.hkjoin lobby
.hksay Привіт усім!
.hkhistory lobby
```

Кімнати мають назви з `a-z`, цифр, `_` або `-`. Нові повідомлення з підключених
кімнат надходять у «Збережені повідомлення»; це вимикається через
`.config HikkaNetChat`. HMAC автентифікує відправника, але не шифрує вміст —
через Інтернет використовуй HTTPS або VPN.

HikkaNetChat 2.0 реєструє короткоживу присутність у кімнатах, показує online-
учасників і глобальний список кімнат із кількістю повідомлень за 24 години.
Нові повідомлення всіх підписаних кімнат читаються одним cursor-потоком, тому
кількість API-запитів не росте разом із кількістю кімнат.

```text
.hkrooms
.hkmembers lobby
```

## GroupAdmin

`group_admin.py` додає owner-only панель керування групою або каналом. Якщо
відкрити `.gadmin` у відповідь на повідомлення користувача, у панелі з'являться
кнопки mute/unmute, warn/unwarn, kick, ban і unban. Окремі екрани керують slow
mode, дозволами звичайних учасників і закріпленням повідомлень. Небезпечні дії
потребують підтвердження, а паралельні натискання захищені блокуванням.

Командні скорочення мають префікс `g`, щоб не перезаписувати стандартні модулі
Hikka: `.gban`, `.gkick`, `.gmute`, `.gwarn`, `.gslowmode`, `.glock` тощо.

```text
.dlmod https://raw.githubusercontent.com/Splaueef/host/main/group_admin.py
```

## MiniGames

`minigames.py` містить публічні для учасників чату inline-ігри: хрестики-нулики,
камінь–ножиці–папір із прихованим вибором, кубик-дуель, шашки 8×8, шахи, ґо на
дошках 9×9 і 13×13 та швидку вікторину. У шашках підтримуються обов'язкове
взяття, серії взяттів, удари назад звичайною шашкою та «літаючі» дамки. Шахи
підтримують шах, мат, пат, рокіровку, взяття на проході, перетворення пішака,
правило 50 ходів і триразове повторення позиції. На Telegram із підтримкою Bot
API 10.3 шахова панель показується як справжнє **Rich Message**: компактна
смугаста таблиця з інтерактивними клітинками та кнопками прямо всередині
повідомлення. Є координати, підсвічування ходів, перевертання дошки та
підтвердження завершення партії; у HikkaNet чорні автоматично бачать свою
сторону знизу. Якщо Rich Messages недоступні, модуль автоматично повертається
до сумісної inline-дошки.
У ґо є захоплення груп,
заборона самогубства й повторення позиції, пас і китайський підрахунок території
з комі 6.5. Завершені матчі потрапляють у локальний рейтинг чату. Меню
відкривається командою `.games`; суперника можна запросити відповіддю або через
`@username`.

У тому самому меню є режим **HikkaNet**. У ньому суперники можуть перебувати в
різних Telegram-чатах і користуватися різними Hikka. Сервер атомарно приймає
приєднання, перевіряє чергу, допустимість кожного ходу, мат/пат, обов'язкове
взяття в шашках, ko/самогубство та підрахунок ґо. Revision захищає від двох
одночасних ходів, а завершення зараховується в глобальні wins/losses/draws та
Elo лише один раз. Відкриті панелі оновлюються у фоні; про запрошення і свій хід
модуль повідомляє у «Збережених повідомленнях».

```text
.ttt @username
.rps
.dicegame
.checkers @username
.chess @username
.go @username
.go9 @username
.go13 @username
.quiz
.gametop
.netgames
.netgame chess
.netgame chess hikka-friend
.netjoin ng_0123456789abcdef
.gametop global chess
.netprofile
```

Прямі команди також приймають `net`: `.chess net [instance-id]`,
`.checkers net`, `.ttt net`, `.go9 net` або `.go13 net`. Без `net` старий
локальний режим працює як раніше.

```text
.dlmod https://raw.githubusercontent.com/Splaueef/host/main/minigames.py
```

## MessageScheduler

`messagescheduler.py` надсилає повідомлення від акаунта Hikka в окремі чати,
групи або канали за разовим, щоденним чи щотижневим розкладом. Кожне завдання
має власні чат, текст і час; доступні список, редагування, пауза, ручний запуск
і повторні спроби після тимчасових помилок.

```text
.ms add @chat once 2026-09-12 18:30 | Разове повідомлення
.ms add тут daily 09:00 | Доброго ранку!
.ms add @chat weekly пн,ср,пт 20:00 | Щотижневе повідомлення
.ms help
```

Встановити окремо:

```text
.dlmod https://raw.githubusercontent.com/Splaueef/host/main/messagescheduler.py
```

## SystemInfo

`sysinfo.py` показує ОС, ядро, середовище, CPU, load average, RAM, диск,
uptime та версії Python/Telethon. Команда модуля — `.sysinfo`; вона навмисно
не використовує `.info`, оскільки це вбудована команда Hikka.

```text
.dlmod https://raw.githubusercontent.com/Splaueef/host/main/sysinfo.py
```

## AlwaysOnline

`alwaysonline.py` підтримує статус Telegram «онлайн», доки працює Hikka.
Модуль реагує на власний статус «офлайн» і швидко повертає «онлайн», а
резервний heartbeat виконується кожні 30 секунд. Також він обробляє FloodWait,
мережеві помилки та безпечно скасовує автовідновлення під час вимкнення.

Команди: `.onlineon`, `.onlineoff`, `.onlinestatus`.

```text
.dlmod https://raw.githubusercontent.com/Splaueef/host/main/alwaysonline.py
```

## HiddenGifts

`hidden_gifts.py` відкриває owner-only каталог з усіма звичайними подарунками,
які Telegram дозволяє купувати зараз, і окремий розділ з 11 прихованими
історичними подарунками, зокрема білим ведмедиком із серцем. Актуальний список,
ціни, Premium-обмеження та залишки тиражу модуль отримує через
`payments.getStarGifts`; розпродані, заблоковані та аукціонні позиції не показує.

Оплата використовує офіційний MTProto-процес `payments.getPaymentForm` →
`payments.sendStarsForm`: спочатку Telegram перевіряє подарунок і повертає точну
ціну в Stars, а списання можливе лише після окремого підтвердження. Перед
оплатою форма перевіряється повторно; якщо сума змінилася, платіж зупиняється.

```text
.hgift @username
.hgift @username | Зі святом!
.hgiftcheck @username
```

У приватному чаті одержувача можна не вказувати; у групі команда також працює
у відповідь на повідомлення користувача. Налаштування `hide_name_by_default`
визначає початковий стан анонімності, який можна змінити у вікні підтвердження.

```text
.dlmod https://raw.githubusercontent.com/Splaueef/host/main/hidden_gifts.py
```

Original projects that used these resources include:

- https://github.com/hikariatama/Hikka
- https://github.com/hikariatama/ftg

This repository may contain:

• mirrored hosting resources  
• configuration adjustments  
• replacements for broken or outdated links  
• additional files required for custom builds  

All original credits belong to the respective authors of the Hikka project and its related repositories.
