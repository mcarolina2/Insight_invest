#!/usr/bin/env bash
# Script de build usado pelo Render a cada deploy
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py configurar_papeis          # grupo Analista e permissões
python manage.py garantir_superusuario      # usa as variáveis DJANGO_SUPERUSER_*
