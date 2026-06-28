# Шпаргалка для собеса — INFER_HUB

Простыми словами, что говорить если спросят про проект.

---

## Что это одной фразой

«Gateway для локального LLM: клиенты шлют задачи в API, worker гоняет inference на GPU через Ollama, результат забирают по job id или SSE.»

---

## Зачем очередь, если Ollama и так API?

Ollama один на машину. Если 10 клиентов шлют запросы одновременно — GPU и RAM забиваются, ответы тормозят или падают. Очередь:
- выравнивает нагрузку
- даёт **приоритеты** (важные задачи первыми)
- retry при сбое
- история задач в SQLite

---

## Почему SQLite, а не Postgres?

Pet-project / single-node setup. SQLite достаточно для одного worker + одного API на локальной машине. На собесе можно сказать: «для прода масштабирования вынес бы jobs в Postgres + Redis queue».

---

## Как worker забирает задачи без гонок?

`BEGIN IMMEDIATE` в SQLite + `UPDATE ... WHERE status = pending`. Два worker'а не возьмут одну задачу — атомарный claim batch.

---

## Circuit breaker — что это?

Если Ollama 5 раз подряд упал, API временно не долбит его новыми запросами (~30 сек). Даёт Ollama восстановиться.

---

## GPU vs CPU

Inference идёт в Ollama с `num_gpu` layers — модель на видеокарте. CPU только для API, SQLite, очереди — и то ограничен 70–75% через affinity и env потоков BLAS.

---

## Что показать живьём

```bash
./scripts/run_api.sh
./scripts/run_worker.sh
./scripts/demo.sh
curl http://127.0.0.1:8080/health | jq
open http://127.0.0.1:8080/docs
```

---

## Типичные вопросы

**Почему FastAPI?**  
Async, OpenAPI из коробки, SSE для стрима, быстро поднять REST.

**Что если worker упал?**  
Задачи остаются `running` — в v2 добавил бы heartbeat + requeue stale jobs.

**Как масштабировать?**  
Несколько worker'ов + Redis queue + Postgres. Сейчас single-node by design.

**Безопасность?**  
Optional `API_KEY` в заголовке `X-API-Key`, rate limit по IP.

---

## Чего не говорить

- «Написал за 2 минуты»
- «AI всё сделал, я не знаю»
- «Это прод на миллион пользователей»

Говори честно: **личный проект, local GPU inference gateway, доводил бы дальше для прода.**
