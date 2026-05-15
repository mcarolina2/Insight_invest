"""
Script de diagnóstico — rode ANTES de tentar o management command.
Salve como: Insight_invest/verificar_setup.py
Execute:    python verificar_setup.py
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

print("=" * 55)
print("DIAGNÓSTICO DO SETUP — Insight Invest")
print("=" * 55)

# ── 1. Verifica estrutura de pastas ──────────────────────
pastas_necessarias = [
    "market_data/management",
    "market_data/management/commands",
    "services",
    "services/extractors",
]

print("\n[1] Estrutura de pastas:")
for pasta in pastas_necessarias:
    caminho = os.path.join(BASE_DIR, pasta)
    existe  = os.path.isdir(caminho)
    status  = "✓" if existe else "✗ FALTANDO"
    print(f"  {status}  {pasta}/")
    if not existe:
        os.makedirs(caminho, exist_ok=True)
        print(f"       → pasta criada automaticamente")

# ── 2. Verifica arquivos __init__.py ─────────────────────
inits_necessarios = [
    "market_data/management/__init__.py",
    "market_data/management/commands/__init__.py",
    "services/__init__.py",
    "services/extractors/__init__.py",
]

print("\n[2] Arquivos __init__.py (obrigatórios):")
for init in inits_necessarios:
    caminho = os.path.join(BASE_DIR, init)
    existe  = os.path.isfile(caminho)
    status  = "✓" if existe else "✗ FALTANDO"
    print(f"  {status}  {init}")
    if not existe:
        open(caminho, "w").close()  # cria arquivo vazio
        print(f"       → criado automaticamente")

# ── 3. Verifica arquivos principais ──────────────────────
arquivos_principais = {
    "market_data/management/commands/load_macro_data.py":  "Management command BCB",
    "services/extractors/bcb_extractor.py":                "Extrator BCB",
    "services/extractors/yfinance_extractor.py":           "Extrator yfinance",
    "services/calculators/indicadores.py":                  "Calculador de KPIs",
}

print("\n[3] Arquivos principais:")
todos_ok = True
for arquivo, descricao in arquivos_principais.items():
    caminho = os.path.join(BASE_DIR, arquivo)
    existe  = os.path.isfile(caminho)
    status  = "✓" if existe else "✗ FALTANDO"
    if not existe:
        todos_ok = False
    print(f"  {status}  {arquivo:<52}  ({descricao})")

# ── 4. Verifica dependências Python ──────────────────────
print("\n[4] Dependências Python:")
deps = ["django", "requests", "pandas", "yfinance", "beautifulsoup4", "transformers"]
for dep in deps:
    try:
        importlib_name = dep.replace("-", "_").replace("beautifulsoup4", "bs4")
        __import__(importlib_name)
        print(f"  ✓  {dep}")
    except ImportError:
        print(f"  ✗  {dep}  ← rode: pip install {dep}")

# ── 5. Tenta importar o extrator BCB ─────────────────────
print("\n[5] Teste de import:")
sys.path.insert(0, BASE_DIR)
try:
    from services.extractors.bcb_extractor import consolidar_series
    print("  ✓  services.extractors.bcb_extractor importado com sucesso")
except Exception as e:
    print(f"  ✗  Falha no import: {e}")

# ── 6. Resumo ─────────────────────────────────────────────
print("\n" + "=" * 55)
if todos_ok:
    print("✓ Setup OK! Agora execute:")
    print("  python manage.py load_macro_data --meses 12")
else:
    print("✗ Corrija os arquivos FALTANDO acima e rode de novo.")
print("=" * 55)