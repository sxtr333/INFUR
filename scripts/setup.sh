#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

echo
echo "=== INFER_HUB setup ==="
echo
echo "API_KEY (в .env):"
echo "  • Оставь ПУСТЫМ — для локального демо без заголовков (проще всего)"
echo "  • Или задай свой ключ, например:  API_KEY=sxtr333-infer-demo"
echo "    тогда в запросах нужен заголовок:  X-API-Key: sxtr333-infer-demo"
echo
echo "Ollama API key НЕ нужен — локальный Ollama на 127.0.0.1:11434 без ключа."
echo
echo "Запуск:"
echo "  ./scripts/run_api.sh      # терминал 1"
echo "  ./scripts/run_worker.sh   # терминал 2 (GPU)"
echo "  ./scripts/demo.sh         # проверка"
echo
