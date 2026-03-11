#!/usr/bin/env python3
import csv
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import oracledb

# Конфигурация через переменные окружения.
# Обязательные:
#   ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN
# Опциональные:
#   POLL_SECONDS, STATE_FILE, OUTPUT_CSV, START_FROM_SCN, CALL_TIMEOUT_MS
ORACLE_USER = os.getenv("ORACLE_USER", "")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD", "")
ORACLE_DSN = os.getenv("ORACLE_DSN", "")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
STATE_FILE = Path(os.getenv("STATE_FILE", "./logminer_state.json"))
OUTPUT_CSV = Path(os.getenv("OUTPUT_CSV", "./logminer_changes.csv"))
START_FROM_SCN_ENV = os.getenv("START_FROM_SCN")
# Для снижения риска зависаний админ-команд
CALL_TIMEOUT_MS = int(os.getenv("CALL_TIMEOUT_MS", "30000"))

RUNNING = True


def handle_shutdown(signum, _frame):
    # Обработчик SIGINT/SIGTERM: переключает флаг остановки цикла.
    global RUNNING
    RUNNING = False
    print(f"[logminer] received signal={signum}, shutting down...")


def utc_now() -> str:
    # Возвращает текущее UTC-время в ISO-формате.
    return datetime.now(timezone.utc).isoformat()


def validate_env() -> None:
    # Проверяет наличие обязательных переменных окружения.
    missing = []
    if not ORACLE_USER:
        missing.append("ORACLE_USER")
    if not ORACLE_PASSWORD:
        missing.append("ORACLE_PASSWORD")
    if not ORACLE_DSN:
        missing.append("ORACLE_DSN")
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


def load_state() -> Dict[str, int]:
    # Загружает watermark (last_commit_scn) из state-файла.
    if STATE_FILE.exists():
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)

    # Позволяет задать стартовый SCN для первого запуска/бэкфилла.
    if START_FROM_SCN_ENV:
        return {"last_commit_scn": int(START_FROM_SCN_ENV)}

    # Базовое значение: с нуля (если явный START_FROM_SCN не указан).
    return {"last_commit_scn": 0}


def save_state(last_commit_scn: int) -> None:
    # Сохраняет watermark после успешного батча для инкрементального рестарта.
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump({"last_commit_scn": last_commit_scn, "updated_at": utc_now()}, f)


def get_current_scn(cur: oracledb.Cursor) -> int:
    # Читает текущий SCN БД (верхнюю границу диапазона mining).
    cur.execute("SELECT current_scn FROM v$database")
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Unable to fetch current_scn from v$database")
    return int(row[0])


def check_logminer_view(cur: oracledb.Cursor) -> None:
    # Быстрая проверка доступа к V$LOGMNR_CONTENTS (права/видимость).
    cur.execute("SELECT COUNT(*) FROM v$logmnr_contents WHERE 1 = 0")
    _ = cur.fetchone()


def start_logminer(cur: oracledb.Cursor, start_scn: int, end_scn: int) -> None:
    # Запускает LogMiner на ограниченном SCN-диапазоне.
    # COMMITTED_DATA_ONLY исключает незакоммиченные изменения.
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
    # Корректно завершает сессию LogMiner (без падения на повторном вызове).
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
    # Забирает только изменения данных:
    #   1 INSERT, 2 DELETE, 3 UPDATE.
    # Инкремент строится по COMMIT_SCN.
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
        ts = data.get("timestamp")
        if isinstance(ts, datetime):
            data["timestamp"] = ts.isoformat()
        rows.append(data)
    return rows


def append_csv(rows: List[Dict[str, object]]) -> None:
    # Дописывает батч в CSV, сохраняя полный журнал изменений.
    if not rows:
        return

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    file_exists = OUTPUT_CSV.exists()

    fieldnames = [
        "commit_scn",
        "scn",
        "timestamp",
        "seg_owner",
        "table_name",
        "operation",
        "operation_code",
        "redo_sequence",
        "rs_id",
        "ssn",
        "sql_redo",
        "sql_undo",
    ]

    with OUTPUT_CSV.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    # Главный цикл polling: запуск LogMiner, чтение изменений, запись CSV и state.
    validate_env()
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    state = load_state()
    last_commit_scn = int(state.get("last_commit_scn", 0))

    print(
        "[logminer] start",
        f"dsn={ORACLE_DSN}",
        f"poll={POLL_SECONDS}s",
        f"state_file={STATE_FILE}",
        f"output_csv={OUTPUT_CSV}",
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
                    f"[logminer] no new SCN: current_scn={current_scn}, last_commit_scn={last_commit_scn}"
                )
                time.sleep(POLL_SECONDS)
                continue

            # Берем ограниченный SCN-диапазон, чтобы не держать бесконечные
            # mining-сессии и делать ретраи предсказуемыми.
            start_scn = last_commit_scn + 1
            end_scn = current_scn

            start_logminer(cur, start_scn=start_scn, end_scn=end_scn)
            rows = fetch_changes(cur, from_commit_scn=last_commit_scn)
            append_csv(rows)

            if rows:
                # Обновляем watermark строго максимумом COMMIT_SCN из батча.
                new_last_commit_scn = max(int(r["commit_scn"]) for r in rows if r["commit_scn"] is not None)
                last_commit_scn = max(last_commit_scn, new_last_commit_scn)
                save_state(last_commit_scn)

            print(
                f"[logminer] batch done: rows={len(rows)} range=[{start_scn},{end_scn}] "
                f"last_commit_scn={last_commit_scn}"
            )

        except Exception as exc:
            print(f"[logminer] ERROR: {exc}", file=sys.stderr)
            time.sleep(POLL_SECONDS)

        finally:
            # Всегда закрываем LogMiner/курсор/коннект, чтобы не держать ресурсы БД.
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

    print("[logminer] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
