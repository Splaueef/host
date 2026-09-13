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
- підтвердження встановлення, видалення й інших небезпечних дій.

Відкрити панель: `.modmenu`.

Встановити окремо:

```text
.dlmod https://raw.githubusercontent.com/Splaueef/host/main/modulehub.py
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
камінь–ножиці–папір із прихованим вибором, кубик-дуель, шашки 8×8, шахи та
швидку вікторину. У шашках підтримуються обов'язкове взяття, серії взяттів,
удари назад звичайною шашкою та «літаючі» дамки. Шахи підтримують шах, мат,
пат, рокіровку, взяття на проході, перетворення пішака, правило 50 ходів і
триразове повторення позиції. Завершені матчі потрапляють у локальний рейтинг
чату. Меню відкривається командою `.games`; суперника можна запросити відповіддю
або через `@username`.

```text
.ttt @username
.rps
.dicegame
.checkers @username
.chess @username
.quiz
.gametop
```

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
