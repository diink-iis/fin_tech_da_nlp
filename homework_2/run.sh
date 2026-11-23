#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

usage() {
    cat <<'HELP'
Запуск NewsFlow: ./run.sh [--setup] [--offline] [--demo] [--open] [--reload]

  --setup    Установить зависимости и подготовить модель перед запуском.
  --offline  Готовое окружение и локальная модель, без установки пакетов.
  --demo     Подготовить примеры в отдельной базе data/demo.db.
  --open     Открыть интерфейс в браузере после запуска сервера.
  --reload   Перезапускать сервер при изменении кода (для разработки).

Переменные: HOST=127.0.0.1, PORT=8000, VENV_DIR=venv.
PYTHON задаёт Python 3.11/3.12 для создания нового окружения.
RSS_DB_PATH и HF_HOME задают постоянные пути к личной базе и кэшу модели.
В --demo всегда используется отдельная data/demo.db вместо RSS_DB_PATH.
HELP
}

setup=0
reload=0
open_browser=0
demo=0
offline=0
case "${HF_HUB_OFFLINE:-0}" in
    1|true|TRUE|yes|YES|on|ON) offline=1 ;;
esac
while [ "$#" -gt 0 ]; do
    case "$1" in
        --setup) setup=1 ;;
        --offline) offline=1 ;;
        --reload) reload=1 ;;
        --open) open_browser=1 ;;
        --demo) demo=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

venv_dir="${VENV_DIR:-venv}"
created=0
if [ -e "$venv_dir" ] && [ ! -x "$venv_dir/bin/python" ]; then
    echo "В $venv_dir нет рабочего окружения. Укажите другой VENV_DIR; существующий каталог не изменён." >&2
    exit 1
fi
if [ ! -x "$venv_dir/bin/python" ]; then
    if [ "$offline" -eq 1 ]; then
        echo "Нет готового окружения $venv_dir. С интернетом выполните ./run.sh --setup." >&2
        exit 1
    fi
    interpreter="${PYTHON:-}"
    if [ -z "$interpreter" ]; then
        for candidate in python3.12 python3.11 python3; do
            if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)'; then
                interpreter="$candidate"
                break
            fi
        done
    fi
    if [ -z "$interpreter" ] || ! "$interpreter" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)'; then
        echo "Нужен Python 3.11 или 3.12. Укажите его через PYTHON=/путь/к/python3.12." >&2
        exit 1
    fi
    echo "Создание окружения $venv_dir..."
    "$interpreter" -m venv "$venv_dir"
    created=1
fi

python="$venv_dir/bin/python"
if ! "$python" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)'; then
    echo "В $venv_dir нужна версия Python 3.11/3.12. Окружение сохранено." >&2
    echo "Для другого окружения: PYTHON=python3.12 VENV_DIR=venv312 ./run.sh --setup" >&2
    exit 1
fi

echo "=== NewsFlow ==="
"$python" --version

host="${HOST:-127.0.0.1}"
port="${PORT:-8000}"
# Fail before starting the browser helper if this address is already occupied.
"$python" - "$host" "$port" <<'PY'
import socket
import sys

host, raw_port = sys.argv[1:]
try:
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise ValueError("порт должен находиться в диапазоне 1–65535")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for family, kind, protocol, _, address in addresses:
        with socket.socket(family, kind, protocol) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(address)
except (OSError, ValueError) as exc:
    print(f"Нельзя запустить сервер на {host}:{raw_port}: {exc}", file=sys.stderr)
    print("Остановите занявший адрес процесс или выберите, например, PORT=8001.", file=sys.stderr)
    sys.exit(1)
PY

# Проверка метаданных читает только локальные файлы. Готовый запуск не вызывает pip.
dependencies_ready=1
if ! "$python" - <<'PY'
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    from packaging.requirements import Requirement
except ImportError:
    print("Не установлен packaging для проверки зависимостей.", file=sys.stderr)
    sys.exit(1)

problems = []
for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    req = Requirement(line)
    if req.marker and not req.marker.evaluate():
        continue
    try:
        installed = version(req.name)
    except PackageNotFoundError:
        problems.append(f"не установлен {req.name}")
    else:
        if installed not in req.specifier:
            problems.append(f"{req.name}: установлено {installed}, требуется {req.specifier}")
if problems:
    print("\n".join(problems), file=sys.stderr)
    sys.exit(1)
PY
then
    dependencies_ready=0
fi

if [ "$offline" -eq 1 ] && [ "$dependencies_ready" -eq 0 ]; then
    echo "Окружение не готово для запуска без сети. С интернетом выполните ./run.sh --setup." >&2
    exit 1
fi

if [ "$offline" -eq 0 ] && { [ "$setup" -eq 1 ] || [ "$created" -eq 1 ] || [ "$dependencies_ready" -eq 0 ]; }; then
    echo "Установка зависимостей из requirements.txt..."
    "$python" -m pip install -r requirements.txt
    echo "Подготовка модели..."
    "$python" -m scripts.prepare_semantic
else
    echo "Проверка локальной модели..."
    if ! "$python" -m scripts.prepare_semantic --offline; then
        if [ "$offline" -eq 1 ]; then
            echo "Модель не готова в текущем HF_HOME. С интернетом выполните ./run.sh --setup." >&2
            exit 1
        fi
        echo "Локальной модели нет; загрузка в текущий HF_HOME..."
        "$python" -m scripts.prepare_semantic
    fi
fi

# Сервер использует только проверенный кэш модели. Доступ к внешним RSS не ограничен.
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
browser_host="$host"
case "$host" in
    0.0.0.0|::) browser_host=127.0.0.1 ;;
    *:*) browser_host="[$host]" ;;
esac
url="http://$browser_host:$port/"
if [ "$demo" -eq 1 ]; then
    export RSS_DB_PATH="$(pwd)/data/demo.db"
    echo "Деморежим: $RSS_DB_PATH"
    RSS_DEMO_BASE_URL="$url" "$python" -m scripts.prepare_demo
fi

if [ "$open_browser" -eq 1 ]; then
    "$python" - "$url" <<'PY' &
import sys
import time
import urllib.error
import urllib.request
import webbrowser

url = sys.argv[1]
for _ in range(120):
    try:
        with urllib.request.urlopen(url + "api/health", timeout=1) as response:
            ready = response.status == 200
    except (OSError, urllib.error.URLError):
        ready = False
    if ready:
        if not webbrowser.open(url):
            print(f"Откройте интерфейс вручную: {url}", file=sys.stderr)
        break
    time.sleep(0.25)
else:
    print(f"Браузер не открыт: сервер не ответил. Проверьте вывод сервера и адрес {url}", file=sys.stderr)
PY
fi

uvicorn_args=(app.main:app --host "$host" --port "$port")
if [ "$reload" -eq 1 ]; then
    uvicorn_args+=(--reload)
fi
echo "NewsFlow: $url"
exec "$python" -m uvicorn "${uvicorn_args[@]}"
