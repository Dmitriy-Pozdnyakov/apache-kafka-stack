# Apache Kafka 3-Node Cluster (KRaft)

Отдельный проект на `apache/kafka` с 3 брокерами в режиме KRaft, Kafka UI и нагрузочным тестом на Python.

## Подготовка к GitHub

- В репозиторий коммитится только шаблон `.env.example`, локальный `.env` не коммитить.
- Локальные секреты и артефакты TLS (`scripts/tls/*.jks`, `*.crt`) исключены через `.gitignore`.
- Файл `scripts/nginx/vmui_htpasswd` не коммитить; используй скрипт генерации ниже.

После клона репозитория:

```bash
cp .env.example .env
./scripts/nginx/generate-vmui-htpasswd.sh
PUBLIC_HOST=127.0.0.1 ./scripts/tls/generate-kafka-tls.sh
```

## Что разворачивается

- `kafka-1`, `kafka-2`, `kafka-3` (3-нодовый кластер)
- `kafka-init` (инициализация топиков)
- `worker` (консюмер, масштабируется)
- `schema-registry` (хранилище схем Avro/Protobuf/JSON Schema)
- `kafka-ui` (Web UI)
- `kafka-exporter` (метрики)
- `victoria-metrics` (TSDB + VMUI)
- `vmagent` (scrape/exporter -> remote write в VictoriaMetrics)
- `grafana` (готовые dashboard-ы поверх VictoriaMetrics)
- `nginx` (опциональный reverse proxy, через override compose)

## Быстрый старт

Из директории `apache-kafka-stack` доступны 2 варианта:

Перед первым запуском (если `KAFKA_EXTERNAL_SECURITY_PROTOCOL` включает `SSL`) сгенерируй TLS-файлы:

```bash
PUBLIC_HOST=127.0.0.1 ./scripts/tls/generate-kafka-tls.sh
```

Или взять `PUBLIC_HOST` из `.env`:

```bash
set -a; source .env; set +a
./scripts/tls/generate-kafka-tls.sh
```

1. Без nginx (прямой доступ к сервисам):

```bash
docker compose up -d --scale worker=3
```

2. С nginx (единая точка входа):

```bash
docker compose -f docker-compose.yaml -f docker-compose.nginx.yaml up -d --scale worker=3
```

Проверка:

```bash
docker compose ps
```

Остановка:

```bash
docker compose down
```

## Порты

### Без nginx (прямой доступ)

- Kafka broker 1 (host): `19092`
- Kafka broker 2 (host): `19093`
- Kafka broker 3 (host): `19094`
- Kafka UI: `18085`
- Kafka Exporter: `19308`
- Schema Registry: `18081`
- VictoriaMetrics (VMUI/API): `18428`
- Grafana: `13000`

### С nginx override

- Единая точка входа: `http://localhost:18080`
- Корневой маршрут `/` показывает страницу со ссылками на сервисы
- Маршруты:
  - `/kafka-ui/`
  - `/grafana/`
  - `/vmui/`
  - `/schema-registry/`
  - `/kafka-exporter/`

Примечание: в режиме nginx у `kafka-ui` включен `context path` `/kafka-ui`, поэтому открывать нужно именно `/kafka-ui/`.

## Ключевые параметры `.env`

- `BROKER=kafka-1:9092,kafka-2:9092,kafka-3:9092`
- `PUBLIC_HOST=127.0.0.1` (или IPv4 адрес вашей ВМ для удалённых клиентов Kafka)
- `KAFKA_EXTERNAL_SECURITY_PROTOCOL=SASL_SSL` (TLS + логин/пароль для внешнего listener)
- `KAFKA_SSL_KEYSTORE_PASSWORD`, `KAFKA_SSL_KEY_PASSWORD`, `KAFKA_SSL_TRUSTSTORE_PASSWORD`
- `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD` (логин/пароль Kafka для внешних клиентов)
- `KAFKA_CONTROLLER_QUORUM_VOTERS=1@kafka-1:9093,2@kafka-2:9093,3@kafka-3:9093`
- `TOPIC_REPLICATION_FACTOR=3`
- `KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=3`
- `KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=3`
- `KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=2`
- `KAFKA_MIN_INSYNC_REPLICAS=2`

Нюанс: `PUBLIC_HOST` используется в `KAFKA_ADVERTISED_LISTENERS` и в TLS SAN.
Режим TLS в этом проекте IP-only: указывай только IPv4 (без DNS/FQDN), затем пересоздай сертификаты и брокеры.

### Пересоздание брокеров после смены PUBLIC_HOST/TLS

```bash
docker compose up -d --force-recreate kafka-1 kafka-2 kafka-3
```

## Авторизация

- `kafka-ui`:
  - включена форма логина (`KAFKA_UI_AUTH_TYPE=LOGIN_FORM`)
  - логин/пароль: `KAFKA_UI_USER` / `KAFKA_UI_PASSWORD`
- `grafana`:
  - включена форма логина (`GRAFANA_ANONYMOUS_ENABLED=false`)
  - логин/пароль: `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`
- в режиме nginx:
  - basic auth включен только для `VMUI` (`/vmui/`)
  - логин/пароль берутся из `.env` (`VMUI_USER` / `VMUI_PASSWORD`) и генерируются в `scripts/nginx/vmui_htpasswd` через `scripts/nginx/generate-vmui-htpasswd.sh`

## Работа с сообщениями

Отправка:

```bash
./scripts/produce.sh "hello"
./scripts/produce.sh "payment created" "user-42"
```

Логи консюмера:

```bash
docker compose logs -f worker
```

## Инициализация топиков

Скрипт `scripts/init-topics.sh`:

1. ждёт готовность Kafka по `BROKER`;
2. создаёт `TOPIC`, `RETRY_TOPIC`, `DLQ_TOPIC`;
3. применяет `replication-factor` и `partitions`;
4. применяет `retention.ms` и `cleanup.policy`;
5. печатает `--describe` для проверки.

## Нагрузочный тест Python

### Подготовка

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r scripts/python/requirements.txt
```

### Запуск теста

```bash
python3 scripts/python/load_test.py \
  --broker 127.0.0.1:19092,127.0.0.1:19093,127.0.0.1:19094 \
  --topic transactions \
  --group-id load-test \
  --security-protocol SASL_SSL \
  --ssl-cafile ./scripts/tls/ca.crt \
  --sasl-mechanism PLAIN \
  --sasl-username kafka_user \
  --sasl-password change_me_kafka_password \
  --messages 10000 \
  --batch-size 16384 \
  --linger-ms 10 \
  --timeout-sec 180
```

Скрипт выводит summary:

- `messages_produced`
- `messages_consumed`
- `duplicates`
- `lost`
- `producer_msg_per_sec`
- `status` (`OK` или `MISMATCH`)

## Сверка с Kafka UI

1. Открыть Kafka UI: `http://localhost:18085`
2. `Topics` -> выбрать `transactions`
3. Проверить throughput/offset динамику во время теста
4. `Consumer Groups` -> найти группу вида `load-test-group-<run_id>`
5. Сверить итоговые цифры из summary (`produced/consumed`) с отображаемым состоянием UI

Примечание: в UI метрики могут обновляться с небольшой задержкой.

## Schema Registry в Kafka UI

Проверка API Schema Registry:

```bash
curl -s http://localhost:18081/subjects
```

Где смотреть в Kafka UI:

1. Открыть `http://localhost:18085`
2. Выбрать кластер `apache-local`
3. Перейти в раздел `Schemas`

Если схемы пока не публиковались, раздел может быть пустым, это нормально.

## Метрики в VictoriaMetrics

Проверить, что `vmagent` видит `kafka-exporter`:

```bash
curl -s http://localhost:18428/api/v1/targets | jq .
```

Открыть UI VictoriaMetrics:

- `http://localhost:18428/vmui`

Примеры метрик для запроса:

- `kafka_brokers`
- `kafka_topic_partitions`
- `kafka_consumergroup_lag`

## Grafana (дашборды из коробки)

Grafana уже преднастроена через provisioning:

- datasource `VictoriaMetrics` (`http://victoria-metrics:8428`)
- папка `Kafka` с готовыми dashboard-ами
- домашний dashboard по умолчанию: `01 - Kafka Overview`

Открыть:

- `http://localhost:13000`

Логин по умолчанию:

- значения из `.env`: `GRAFANA_ADMIN_USER / GRAFANA_ADMIN_PASSWORD`

Готовые dashboard-ы:

1. `Kafka Overview`
   - Active Brokers
   - Topic Partitions
   - Produced Messages/sec by Topic
   - Consumer Group Lag by Topic
2. `Kafka Transactions Flow`
   - Produced Total (transactions)
   - Consumed Total (transactions-workers)
   - Produced - Consumed (transactions)
   - Produce Rate / Consume Rate
3. `Kafka Consumer Lag TopK`
   - Top 10 Consumer Group Lag
   - Consumer Lag Table
   - Total Consumer Lag

Оба dashboard-а `Kafka Transactions Flow` и `Kafka Consumer Lag TopK` поддерживают фильтры:

- `$topic` (выбор топика, включая `All`)
- `$group` (выбор consumer group, включая `All`)

Для быстрого переключения между dashboard-ами в каждом есть dropdown-ссылка `Kafka Dashboards`.
Также добавлены явные ссылки `01 Overview`, `02 Flow`, `03 Lag`, `Kafka UI`, `VMUI` в верхней части каждого dashboard.
Ссылки `Kafka UI` и `VMUI` сделаны относительными (без хардкода `localhost`) и работают через nginx-маршруты.

## Backup/Restore данных кластера

Backup всех 3 нод:

```bash
./scripts/backup-kafka-volume.sh
```

Скрипт создаёт 3 архива (по одному на брокер) с одинаковым timestamp и выводит его.

Restore всех 3 нод по timestamp:

```bash
./scripts/restore-kafka-volume.sh <timestamp>
```

Пример:

```bash
./scripts/restore-kafka-volume.sh 20260310_120000
```

Опции:

- `BACKUP_DIR=<path>`
- `CLEAR_VOLUME_BEFORE_RESTORE=true|false`

## Oracle LogMiner (без DAG, polling раз в минуту)

Файлы:

- `scripts/oracle_logminer/logminer_checks.sql`
- `scripts/oracle_logminer/logminer_poll.py`
- `scripts/oracle_logminer/requirements.txt`

### Что отвечает на ваши вопросы по аудиту

- Какая таблица изменилась: `SEG_OWNER`, `TABLE_NAME`
- Какая операция: `OPERATION` (`INSERT/UPDATE/DELETE`) или `OPERATION_CODE` (`1/2/3`)
- Какие данные изменены:
  - `SQL_REDO` (что применено),
  - `SQL_UNDO` (как откатить / что было до)
- Отметка времени/порядка:
  - `COMMIT_SCN` — лучший watermark для инкрементального чтения
  - `TIMESTAMP` — человекочитаемая дата/время
  - `SEQUENCE#`, `RS_ID`, `SSN` — порядок внутри redo

### Проверки доступа

```bash
sqlplus <user>/<password>@<dsn> @scripts/oracle_logminer/logminer_checks.sql
```

### Запуск polling-скрипта (каждую 1 минуту)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r scripts/oracle_logminer/requirements.txt

export ORACLE_USER='<user>'
export ORACLE_PASSWORD='<password>'
export ORACLE_DSN='<host>:<port>/<service_name>'
export POLL_SECONDS=60
export STATE_FILE='./scripts/oracle_logminer/logminer_state.json'
export OUTPUT_CSV='./scripts/oracle_logminer/logminer_changes.csv'
# Опционально для первого запуска:
# export START_FROM_SCN=123456789

python scripts/oracle_logminer/logminer_poll.py
```

Скрипт:

1. Берет `current_scn` из `v$database`.
2. Запускает LogMiner на диапазоне SCN.
3. Читает только новые изменения: `commit_scn > last_commit_scn`.
4. Сохраняет строки в CSV в порядке Oracle:
   `ORDER BY commit_scn, sequence#, rs_id, ssn`.
5. Обновляет state-файл с новым `last_commit_scn`.
