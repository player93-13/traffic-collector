# 📡 Traffic Collector

Система сбора и хранения сетевой статистики пользователей из:

* **Xray (VLESS / VMess / Reality и др.)**
* **WireGuard**

Данные сохраняются в PostgreSQL и могут визуализироваться через Grafana.

---

## ⚙️ Архитектура

```
Xray / WireGuard
        ↓
   Collector (Python)
        ↓
   PostgreSQL
        ↓
     Grafana
```

---

## 🚀 Возможности

* 📊 Сбор RX/TX трафика по пользователям
* 🔁 Поддержка Xray stats API
* 🔐 Поддержка WireGuard peer traffic
* 🧠 Кэширование пользователей и состояния (last_stats)
* 📈 Исторические данные (таблица stats)
* 🧾 Вычисление дельты трафика
* 🩺 Health endpoint (`/health`)
* 🐳 Docker deployment
* 📉 Минимальная нагрузка на PostgreSQL

---

## 📦 Быстрый старт

```bash
git clone https://github.com/Player93-13/traffic-collector.git
cd traffic-collector

cp .env.example .env
docker compose up -d
```

---

## ⚡ Быстрая установка (script)

```bash
curl -fsSL https://raw.githubusercontent.com/Player93-13/traffic-collector/main/install.sh | bash
```

---

## ⚙️ Конфигурация (.env)

```env
DB_HOST=postgres.example.local
DB_NAME=traffic
DB_USER=traffic_user
DB_PASS=change_me

INTERVAL=60
CACHE_REFRESH=300

HEALTH_BIND=0.0.0.0
HEALTH_PORT=9229

XRAY_API=http://127.0.0.1:8080
XRAY_BIN=xray

WG_CONTAINER=wg-container
WG_INTERFACE=wg0
```

---

## 🗄 Структура БД

### users

* id
* source (xray / wg)
* external_id

### stats

* ts (timestamp)
* user_id
* rx (bytes)
* tx (bytes)

### last_stats

* user_id
* rx
* tx

---

## 📊 Grafana

После запуска:

```
http://localhost:3000
```

**Login:** admin
**Password:** admin

---

### Пример SQL (Grafana)

```sql
SELECT
  ts,
  user_id,
  rx,
  tx
FROM stats
ORDER BY ts DESC;
```

---

## 🧠 Как работает сбор

### Xray

Использует:

```bash
xray api statsquery --server=http://...
```

Парсит:

```
user>>>email>>>downlink
user>>>email>>>uplink
```

---

### WireGuard

Через:

```bash
docker exec wg-container wg show wg0
```

---

## ⚡ Оптимизации

* In-memory cache пользователей
* In-memory cache last_stats
* Batch insert (execute_values)
* Минимум SQL внутри loop
* Refresh кэша каждые 5 минут

---

## 🔧 Healthcheck

```bash
curl http://localhost:9229/health
```

Ответ:

```
ok
```

---

## 🧩 Деплой

### Запуск

```bash
docker compose up -d
```

### Остановка

```bash
docker compose down
```

### Полное удаление

```bash
docker compose down -v
```

---

## 📈 Производительность

Подходит для:

* тысяч пользователей VPN
* постоянного polling (60s)
* минимальной нагрузки на PostgreSQL

---

## 🧪 Статус проекта

* ✔ MVP / production-ready
* ✔ Dockerized
* ✔ Optimized collector
* ⚠ Single-node
* ⚠ No message queue