# Privacy Filter

Локальный HTTP-сервис для обнаружения и маскирования персональных данных перед отправкой текста из n8n во внешние AI API.

Сервис поднимает FastAPI-обертку над официальным CLI OpenAI Privacy Filter (`opf`) и дополнительно применяет regex-правила для российских резюме: ФИО, email, телефон, дату рождения, адрес, паспорт, СНИЛС, ИНН и похожие номера документов.

## Переменные окружения

Скопируйте `.env.example` в `.env` на VPS и измените значения под свое окружение.

| Переменная | Значение по умолчанию | Для чего используется |
| --- | --- | --- |
| `PRIVACY_FILTER_API_TOKEN` | `change-me` | Опциональный bearer-токен для защиты HTTP API. Если переменная задана, запросы к `/redact` и `/mask` должны содержать заголовок `Authorization: Bearer <token>`. Если оставить переменную пустой, авторизация будет отключена. |
| `OPF_CHECKPOINT` | `/models/privacy_filter` | Путь внутри контейнера, куда официальный OpenAI Privacy Filter сохраняет checkpoint модели. В `docker-compose.yml` этот путь связан с Docker volume `opf-model-cache`, чтобы модель не скачивалась заново после перезапуска контейнера. |
| `OPF_DEVICE` | `cpu` | Устройство для запуска официального OpenAI Privacy Filter CLI. Для обычного VPS используйте `cpu`. Значение `cuda` имеет смысл только на сервере с поддерживаемой NVIDIA GPU и настроенным GPU runtime для Docker. |
| `OPF_OUTPUT_MODE` | `typed` | Режим маскирования, который передается в `opf --output-mode`. `typed` использует типизированные плейсхолдеры, а `redacted` использует более общий стиль редактирования. |
| `OPF_TIMEOUT_SECONDS` | `300` | Максимальное время выполнения одного запуска `opf` в секундах. Если обработка занимает больше времени, API вернет ошибку таймаута. Увеличьте значение для длинных документов или медленного CPU-only VPS. |

## Запуск

```bash
docker compose up --build -d
```

Первый запрос может выполняться дольше обычного, потому что официальный checkpoint Privacy Filter будет скачан в Docker volume `opf-model-cache`. Не удаляйте этот volume между деплоями, если не хотите скачивать модель заново.

## API

Проверка состояния сервиса из контейнера в той же Docker-сети:

```bash
curl http://privacy-filter-api:8000/health
```

Маскирование текста:

```bash
curl -X POST http://privacy-filter-api:8000/redact \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PRIVACY_FILTER_API_TOKEN" \
  -d '{"text":"ФИО: Иванов Иван Иванович, email: ivan@example.com, телефон +7 999 123-45-67"}'
```

В n8n используйте URL:

```text
http://privacy-filter-api:8000/redact
```

Endpoint `/mask` оставлен как совместимый alias для `/redact`.
