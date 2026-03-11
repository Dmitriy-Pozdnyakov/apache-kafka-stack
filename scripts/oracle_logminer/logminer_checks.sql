-- 1) Проверка, что включено supplemental logging (минимум)
SELECT supplemental_log_data_min
FROM v$database;

-- 2) Проверка доступа к представлению V$LOGMNR_CONTENTS
-- Если нет прав, будет ORA-00942 / ORA-01031
SELECT COUNT(*) AS accessible_rows
FROM v$logmnr_contents
WHERE 1 = 0;

-- 3) Пример аналитического запроса по изменениям
-- Какая таблица изменилась, какая операция и какой SQL изменения
SELECT
    commit_scn,
    scn,
    timestamp,
    seg_owner,
    table_name,
    operation,
    sql_redo,
    sql_undo
FROM v$logmnr_contents
WHERE operation_code IN (1, 2, 3)
ORDER BY commit_scn, sequence#, rs_id, ssn;

-- 4) Какие поля отвечают за время и порядок
-- commit_scn     : надёжный watermark для инкрементального чтения
-- timestamp      : человекочитаемая отметка времени
-- sequence#/rs_id/ssn : порядок внутри redo потока

-- 5) Для точечного разбора конкретной таблицы
SELECT
    commit_scn,
    timestamp,
    seg_owner,
    table_name,
    operation,
    sql_redo,
    sql_undo
FROM v$logmnr_contents
WHERE seg_owner = UPPER(:owner)
  AND table_name = UPPER(:table_name)
  AND operation_code IN (1, 2, 3)
ORDER BY commit_scn, sequence#, rs_id, ssn;
