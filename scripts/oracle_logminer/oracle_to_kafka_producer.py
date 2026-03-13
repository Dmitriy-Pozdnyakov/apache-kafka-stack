#!/usr/bin/env python3
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import oracledb
from confluent_kafka import Producer

# -----------------------------
# Конфигурация Oracle-источника
# -----------------------------
# Учетные данные и DSN для подключения к Oracle.
ORACLE_USER = os.getenv("ORACLE_USER", "")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD", "")
ORACLE_DSN = os.getenv("ORACLE_DSN", "")
# Таймаут вызовов к Oracle (мс), чтобы не зависать на админ-командах LogMiner.
CALL_TIMEOUT_MS = int(os.getenv("CALL_TIMEOUT_MS", "30000"))

# --------------------------------
# Конфигурация polling и состояния
# --------------------------------
# Пауза между циклами polling.
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
# Файл, где хранится watermark (последний обработанный COMMIT_SCN).
STATE_FILE = Path(os.getenv("STATE_FILE", "./oracle_kafka_state.json"))
# Опциональный стартовый SCN для первого запуска/бэкфилла.
START_FROM_SCN_ENV = os.getenv("START_FROM_SCN")

# -----------------------------
# Конфигурация Kafka-приемника
# -----------------------------
# Разрешаем использовать либо KAFKA_BROKER, либо BROKER.
KAFKA_BROKER = os.getenv("KAFKA_BROKER", os.getenv("BROKER", ""))
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "oracle.logminer.raw")
KAFKA_SECURITY_PROTOCOL = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").upper()
SSL_CAFILE = os.getenv("SSL_CAFILE", "")
SSL_CHECK_HOSTNAME = os.getenv("SSL_CHECK_HOSTNAME", "true").lower() == "true"
KAFKA_SASL_MECHANISM = os.getenv("KAFKA_SASL_MECHANISM", "PLAIN")
KAFKA_SASL_USERNAME = os.getenv("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASSWORD = os.getenv("KAFKA_SASL_PASSWORD", "")

# ---------------------------
# Поведение Kafka producer-а
# ---------------------------
KAFKA_CLIENT_ID = os.getenv("KAFKA_CLIENT_ID", "oracle-logminer-producer")
# Сколько секунд ждём завершения flush() перед выходом/ошибкой.
KAFKA_FLUSH_TIMEOUT_SEC = int(os.getenv("KAFKA_FLUSH_TIMEOUT_SEC", "30"))

# Глобальный флаг остановки основного цикла (SIGINT/SIGTERM).
RUNNING = True


def handle_shutdown(signum, _frame):
    """Обработчик SIGINT/SIGTERM: просим главный цикл завершиться корректно."""
    global RUNNING
    RUNNING = False
    print(f"[oracle->kafka] received signal={signum}, shutting down...")


def utc_now() -> str:
    """Текущее время UTC в ISO-формате (для служебных метаданных/логов)."""
    return datetime.now(timezone.utc).isoformat()


def validate_env() -> None:
    """Проверяем, что заданы обязательные env-переменные."""
    missing = []
    if not ORACLE_USER:
        missing.append("ORACLE_USER")
    if not ORACLE_PASSWORD:
        missing.append("ORACLE_PASSWORD")
    if not ORACLE_DSN:
        missing.append("ORACLE_DSN")
    if not KAFKA_BROKER:
        missing.append("KAFKA_BROKER or BROKER")

    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


def load_state() -> Dict[str, int]:
    """
    Загружаем watermark из state-файла.

    Приоритет старта:
    1) state-файл,
    2) START_FROM_SCN,
    3) 0.
    """
    if STATE_FILE.exists():
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)

    if START_FROM_SCN_ENV:
        return {"last_commit_scn": int(START_FROM_SCN_ENV)}

    return {"last_commit_scn": 0}


def save_state(last_commit_scn: int) -> None:
    """Сохраняем watermark после успешной публикации батча в Kafka."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump({"last_commit_scn": last_commit_scn, "updated_at": utc_now()}, f)


def get_current_scn(cur: oracledb.Cursor) -> int:
    """Читаем верхнюю границу SCN в БД (на неё ограничиваем окно mining)."""
    cur.execute("SELECT current_scn FROM v$database")
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Unable to fetch current_scn from v$database")
    return int(row[0])


def check_logminer_view(cur: oracledb.Cursor) -> None:
    """Проверяем доступ к V$LOGMNR_CONTENTS (права/видимость представления)."""
    cur.execute("SELECT COUNT(*) FROM v$logmnr_contents WHERE 1 = 0")
    _ = cur.fetchone()


def start_logminer(cur: oracledb.Cursor, start_scn: int, end_scn: int) -> None:
    """
    Запускаем LogMiner на диапазоне SCN.

    Включаем:
    - DICT_FROM_ONLINE_CATALOG: использовать онлайн-словарь БД;
    - COMMITTED_DATA_ONLY: только закоммиченные транзакции.
    """
    plsql = """
    BEGIN
      DBMS_LOGMNR.START_LOGMNR(
        startScn => :start_scn,
        endScn   => :end_scn,
        options  => DBMS_LOGMNR.DICT_FROM_ONLINE_CATALOG + DBMS_LOGMNR.COMMITTED_DATA_ONLY
      );
    END;
    """
    cur.call_timeout = CALL_TIMEOUT_MS
    cur.execute(plsql, {"start_scn": start_scn, "end_scn": end_scn})


def end_logminer(cur: oracledb.Cursor) -> None:
    """
    Завершаем сессию LogMiner.

    Ошибки при завершении гасим, чтобы не блокировать cleanup.
    """
    plsql = """
    BEGIN
      DBMS_LOGMNR.END_LOGMNR;
    EXCEPTION
      WHEN OTHERS THEN
        NULL;
    END;
    """
    cur.execute(plsql)


def fetch_changes(cur: oracledb.Cursor, from_commit_scn: int) -> List[Dict[str, object]]:
    """
    Читаем изменения из LogMiner.

    Берём только DML:
    - 1 = INSERT
    - 2 = DELETE
    - 3 = UPDATE

    Важный порядок сортировки, чтобы сохранять естественный порядок redo-событий.
    """
    sql = """
    SELECT
      commit_scn,
      scn,
      timestamp,
      seg_owner,
      table_name,
      operation,
      operation_code,
      sequence# AS redo_sequence,
      rs_id,
      ssn,
      sql_redo,
      sql_undo
    FROM v$logmnr_contents
    WHERE operation_code IN (1, 2, 3)
      AND commit_scn > :from_commit_scn
    ORDER BY commit_scn, sequence#, rs_id, ssn
    """
    cur.execute(sql, {"from_commit_scn": from_commit_scn})

    rows: List[Dict[str, object]] = []
    columns = [c[0].lower() for c in cur.description]
    for record in cur:
        data = dict(zip(columns, record))
        # Нормализуем datetime в строку ISO для безопасной сериализации в JSON.
        ts = data.get("timestamp")
        if isinstance(ts, datetime):
            data["timestamp"] = ts.isoformat()
        rows.append(data)
    return rows


def build_kafka_config() -> Dict[str, object]:
    """Собираем конфиг Producer (SASL/SSL включаются по env)."""
    cfg: Dict[str, object] = {
        "bootstrap.servers": KAFKA_BROKER,
        "client.id": KAFKA_CLIENT_ID,
        "acks": "all",
        # Включаем идемпотентность, чтобы снизить риск дублей при ретраях producer.
        "enable.idempotence": True,
        "linger.ms": 10,
    }

    cfg["security.protocol"] = KAFKA_SECURITY_PROTOCOL

    if KAFKA_SECURITY_PROTOCOL in {"SSL", "SASL_SSL"}:
        if SSL_CAFILE:
            cfg["ssl.ca.location"] = SSL_CAFILE
        cfg["ssl.endpoint.identification.algorithm"] = (
            "https" if SSL_CHECK_HOSTNAME else "none"
        )

    if KAFKA_SECURITY_PROTOCOL in {"SASL_SSL", "SASL_PLAINTEXT"}:
        cfg["sasl.mechanism"] = KAFKA_SASL_MECHANISM
        cfg["sasl.username"] = KAFKA_SASL_USERNAME
        cfg["sasl.password"] = KAFKA_SASL_PASSWORD

    return cfg


def kafka_key(row: Dict[str, object]) -> str:
    """
    Формируем стабильный ключ сообщения.

    Это помогает:
    - получать детерминированное распределение по partition,
    - сохранять порядок для одного и того же ключа в рамках partition.
    """
    return (
        f"{row.get('seg_owner','')}.{row.get('table_name','')}|"
        f"{row.get('commit_scn','')}|{row.get('redo_sequence','')}|"
        f"{row.get('rs_id','')}|{row.get('ssn','')}"
    )


def publish_rows(producer: Producer, rows: List[Dict[str, object]]) -> Tuple[int, int]:
    """
    Публикуем батч строк в Kafka.

    Возвращаем пары счётчиков:
    - delivered: доставлено успешно,
    - failed: доставка с ошибкой.
    """
    delivered = 0
    failed = 0

    def on_delivery(err, _msg):
        nonlocal delivered, failed
        if err is not None:
            failed += 1
        else:
            delivered += 1

    for row in rows:
        producer.produce(
            KAFKA_TOPIC,
            key=kafka_key(row).encode("utf-8"),
            value=json.dumps(row, ensure_ascii=False).encode("utf-8"),
            callback=on_delivery,
        )
        # Poll нужен, чтобы исполнять callbacks доставки.
        producer.poll(0)

    # Ждём завершения отправки всех сообщений батча.
    producer.flush(KAFKA_FLUSH_TIMEOUT_SEC)
    return delivered, failed


def main() -> int:
    """
    Главный цикл:
    1) читаем watermark,
    2) запускаем LogMiner на новом SCN-окне,
    3) отправляем события в Kafka,
    4) обновляем watermark только при полном успешном батче.
    """
    validate_env()
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    state = load_state()
    last_commit_scn = int(state.get("last_commit_scn", 0))

    producer = Producer(build_kafka_config())

    print(
        "[oracle->kafka] start",
        f"dsn={ORACLE_DSN}",
        f"topic={KAFKA_TOPIC}",
        f"poll={POLL_SECONDS}s",
        f"state_file={STATE_FILE}",
        f"start_commit_scn={last_commit_scn}",
    )

    while RUNNING:
        conn: Optional[oracledb.Connection] = None
        cur: Optional[oracledb.Cursor] = None

        try:
            conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
            cur = conn.cursor()
            check_logminer_view(cur)

            current_scn = get_current_scn(cur)
            if current_scn <= last_commit_scn:
                print(
                    f"[oracle->kafka] no new SCN: current_scn={current_scn}, last_commit_scn={last_commit_scn}"
                )
                time.sleep(POLL_SECONDS)
                continue

            # Окно обработки в рамках одного polling-цикла.
            start_scn = last_commit_scn + 1
            end_scn = current_scn

            start_logminer(cur, start_scn=start_scn, end_scn=end_scn)
            rows = fetch_changes(cur, from_commit_scn=last_commit_scn)

            if not rows:
                print(
                    f"[oracle->kafka] no data rows in range=[{start_scn},{end_scn}], last_commit_scn={last_commit_scn}"
                )
            else:
                delivered, failed = publish_rows(producer, rows)
                if failed > 0:
                    # Watermark не двигаем: батч будет перечитан (at-least-once).
                    raise RuntimeError(f"Kafka delivery failures: {failed} out of {len(rows)}")

                # Двигаем watermark только на max(COMMIT_SCN) успешно доставленного батча.
                new_last_commit_scn = max(
                    int(r["commit_scn"]) for r in rows if r.get("commit_scn") is not None
                )
                last_commit_scn = max(last_commit_scn, new_last_commit_scn)
                save_state(last_commit_scn)

                print(
                    f"[oracle->kafka] batch done rows={len(rows)} delivered={delivered} "
                    f"range=[{start_scn},{end_scn}] last_commit_scn={last_commit_scn}"
                )

        except Exception as exc:
            print(f"[oracle->kafka] ERROR: {exc}", file=sys.stderr)
            # На ошибке делаем паузу и пробуем снова.
            time.sleep(POLL_SECONDS)

        finally:
            # Важно всегда чистить ресурсы Oracle-сессии.
            if cur is not None:
                try:
                    end_logminer(cur)
                except Exception:
                    pass
                cur.close()
            if conn is not None:
                conn.close()

        if RUNNING:
            time.sleep(POLL_SECONDS)

    # Перед выходом пытаемся дослать буфер producer.
    producer.flush(KAFKA_FLUSH_TIMEOUT_SEC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
