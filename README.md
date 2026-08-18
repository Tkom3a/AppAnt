# УВЫ control

Мониторинг и перезагрузка асиков с iPhone и Windows.

Ядро на сервере раз в 10 минут спрашивает хэшрейт. Если он упал ниже вашего порога — приходит пуш. Если падение сильное или асик молчит — ядро само перезагружает его и 10 минут не трогает.

```
iPhone / Windows  →  ядро (сервер)  →  асики по статическому IP и портам
                           ↑
                    логины/пароли только здесь, в .env
```

Поддерживаются:

- stock **Antminer** (S19k Pro и другие с CGI)
- **Vnish** (`/api/v1`)

---

## 1. Что нужно заранее

1. Сервер, который видит асики (VPS или компьютер на локации). Нужен Python 3.11+ **или** Docker.
2. Статический IP локации.
3. На роутере локации проброс портов: каждый асик → свой внешний порт (обычно на веб-интерфейс асика, порт 80).
4. Логин/пароль веб-интерфейса асиков.

Проверка с сервера, что асик доступен:

```bash
# Antminer
curl --digest -u root:ПАРОЛЬ http://СТАТИЧЕСКИЙ_IP:ПОРТ/cgi-bin/summary.cgi

# Vnish
curl -X POST http://СТАТИЧЕСКИЙ_IP:ПОРТ/api/v1/unlock -H "Content-Type: application/json" -d "{\"pw\":\"admin\"}"
```

Если curl отвечает JSON — порт и пароль верные.

---

## 2. Настройка файлов на сервере

Скопируйте шаблоны, если ещё не скопированы:

```bash
cp .env.example .env
cp miners.json.example miners.json
```

### Файл `.env` — пароли живут только на сервере

Откройте `.env` и заполните:

| Переменная | Что писать |
|---|---|
| `API_TOKEN` | Секрет для iPhone. Любая длинная строка |
| `LOCATION_IP` | Статический IP локации |
| `ANTMINER_USER` / `ANTMINER_PASSWORD` | Обычно `root` / ваш пароль |
| `VNISH_PASSWORD` | Обычно `admin` |
| `AUTO_REBOOT_ENABLED` | `true` — ядро само ребутает |
| `HASHRATE_ALERT_RATIO` | `0.7` — пуш, если ниже 70% от нормы |
| `HASHRATE_REBOOT_RATIO` | `0.3` — авторебут, если ниже 30% или тишина |
| `NTFY_TOPIC` | Секретная тема для пушей на iPhone (см. ниже) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Необязательно, дубль пушей в Telegram |

Если у одного асика другой пароль:

```env
MINER_ASIC1_PASSWORD=другой_пароль
```

`ASIC1` — это `id` из `miners.json`, заглавными буквами.

### Файл `miners.json` — список асиков

Порты и пороги, **без паролей**:

```json
[
  {
    "id": "asic1",
    "name": "S19k Pro #1",
    "firmware": "antminer",
    "port": 8081,
    "expected_ths": 120,
    "auto_reboot": true
  },
  {
    "id": "vnish1",
    "name": "Vnish #1",
    "firmware": "vnish",
    "port": 8090,
    "expected_ths": 120,
    "auto_reboot": true
  }
]
```

`expected_ths` — нормальный хэшрейт в TH/s. Для S19k Pro обычно около 120.

---

## 3. Запуск ядра на сервере

### Вариант A — Docker (удобнее)

На сервере:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose
cd /opt
# скопируйте сюда папку проекта, затем:
cd asic-control   # или Apple — как назовёте папку
nano .env
nano miners.json
docker compose up -d --build
docker compose logs -f
```

Ядро слушает порт **8486**. Откройте в браузере: `http://IP-СЕРВЕРА:8486`

Остановка: `docker compose down`. Обновление после правок: `docker compose up -d --build`.

### Вариант B — Python без Docker

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
cd /opt/asic-control
python3 -m venv .venv
source .venv/bin/activate
pip install -r core/requirements.txt
cd core
uvicorn main:app --host 0.0.0.0 --port 8486
```

Чтобы работало после перезагрузки сервера:

```bash
sudo cp core/asic-control.service /etc/systemd/system/asic-control.service
# поправьте пути в unit-файле, если папка не /opt/asic-control
sudo systemctl daemon-reload
sudo systemctl enable --now asic-control
sudo systemctl status asic-control
```

На роутере/файрволе сервера откройте порт 8486 (или поставьте nginx с HTTPS, см. ниже).

---

## 4. Установка на iPhone

Настоящее приложение (иконка УВЫ, ставится с Windows по кабелю): **[IOS-INSTALL.md](IOS-INSTALL.md)**.

Ярлык Safari без установки IPA: **[IPHONE.md](IPHONE.md)**.

---

## 4.1. Установка на Windows

Двойной щелчок по **`windows-app\install.bat`**.

Установщик сам:

- найдёт Python или поставит его через winget
- поставит зависимости
- скопирует программу в `%LOCALAPPDATA%\UVYControl`
- положит на рабочий стол ярлык **УВЫ control** с вашим логотипом S19

После этого открывайте приложение с рабочего стола. В окне введите `http://IP-СЕРВЕРА:8486` и `API_TOKEN` из `.env`.

Удаление: ярлык в меню Пуск или файл `%LOCALAPPDATA%\UVYControl\uninstall.bat`.

Закрытие окна сворачивает программу в трей. Полный выход — правый клик по иконке рядом с часами → **Выход**.


---

## 5. Пуш-уведомления на iPhone

### ntfy — самый простой рабочий пуш

1. App Store → приложение **ntfy**.
2. Subscribe to topic — придумайте длинную секретную тему, например `farm-reboot-x7k9q`.
3. Эту же строку впишите в `.env`:

```env
NTFY_TOPIC=farm-reboot-x7k9q
```

4. Перезапустите ядро (`docker compose restart`).

Когда хэшрейт упадёт или ядро уйдёт в авторебут, на iPhone придёт уведомление. На Windows то же событие всплывёт в системном уведомлении, если запущено приложение из `windows-app`.

### Telegram (по желанию)

1. В Telegram: [@BotFather](https://t.me/BotFather) → `/newbot` → скопируйте токен.
2. Напишите своему боту `/start`.
3. Узнайте chat id: [@userinfobot](https://t.me/userinfobot) или откройте  
   `https://api.telegram.org/botТОКЕН/getUpdates`
4. В `.env`:

```env
TELEGRAM_BOT_TOKEN=123:ABC
TELEGRAM_CHAT_ID=123456789
```

### Expo-пуши

Если пользуетесь Expo Go / самостоятельной сборкой, приложение само отправит push-токен на ядро при первом запуске.

---

## 6. Как это работает

| Ситуация | Что делает ядро |
|---|---|
| Проверка каждые 10 минут | Читает хэшрейт всех асиков |
| Хэшрейт < 70% нормы | Пуш |
| Хэшрейт < 30% нормы или асик не отвечает | Пуш + авторебут (если включён) |
| После ребута | 10 минут пауза, повторно не ребутает |
| 3 авторебута за 2 часа | Останавливает цикл и пишет критичное уведомление |
| Кнопка в приложении | Ребут сразу, независимо от автологики |

Пароли асиков **не уходят** на iPhone. Телефон ходит только в ядро с `API_TOKEN`.

---

## 7. HTTPS (необязательно, но лучше)

Если есть домен, на сервере с nginx:

```nginx
server {
    listen 443 ssl;
    server_name asic.yourdomain.com;
    ssl_certificate     /etc/letsencrypt/live/asic.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/asic.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8486;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Тогда на iPhone открываете `https://asic.yourdomain.com`.

---

## 8. Если что-то не работает

- **401 в приложении** — неверный `API_TOKEN`.
- **Асик offline / timeout** — с сервера ядра проверьте curl из раздела 1. Часто забывают проброс порта или пишут LAN-адрес вместо белого IP.
- **Vnish auth failed** — пароль не `admin`, пропишите `VNISH_PASSWORD` или `MINER_VNISH1_PASSWORD`.
- **Antminer auth failed** — нужен digest-пароль веб-морды, не воркер пула.
- **Авторебут не срабатывает** — тумблер в приложении выключен, или асик ещё в 10-минутном cooldown, или сработал лимит 3 ребутов.
- **Хэшрейт странный** — поправьте `expected_ths` в `miners.json`. Ядро само отличает GH/s от TH/s по этому числу.

Логи Docker: `docker compose logs -f`
Логи systemd: `journalctl -u asic-control -f`
