#!/bin/bash

set -e

cd "$(dirname "$0")"

echo "=== RSS Aggregator ==="

# Проверяем наличие Python 3.11
if ! command -v python3.11 &> /dev/null; then
    echo "Ошибка: Python 3.11 не установлен."
    echo "Установи Python 3.11 и повтори запуск."
    exit 1
fi

PYTHON=python3.11

echo "Используется Python:"
$PYTHON --version

# Создаём виртуальное окружение, если его нет
if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    $PYTHON -m venv venv
fi

# Активируем окружение
source venv/bin/activate

# Проверяем версию Python внутри окружения
if ! python -c "import sys; exit(0 if sys.version_info[:2] == (3,11) else 1)"; then
    echo "Пересоздание окружения на Python 3.11..."
    deactivate 2>/dev/null || true
    rm -rf venv
    $PYTHON -m venv venv
    source venv/bin/activate
fi

# Обновляем pip
python -m pip install --upgrade pip

# Устанавливаем зависимости
echo "Установка зависимостей..."
pip install -r requirements.txt

# Подготовка семантической модели
echo "Подготовка семантической модели..."
python -m scripts.prepare_semantic

# Запуск веб-приложения
echo "Запуск сервера..."
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
