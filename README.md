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

Original projects that used these resources include:

- https://github.com/hikariatama/Hikka
- https://github.com/hikariatama/ftg

This repository may contain:

• mirrored hosting resources  
• configuration adjustments  
• replacements for broken or outdated links  
• additional files required for custom builds  

All original credits belong to the respective authors of the Hikka project and its related repositories.
