# Experimentos do TCC (rodar na sua máquina, com o banco local)

Estes comandos são pesados (baixam anos de preços de centenas de ações) e por isso
rodam localmente, não no Render. Os resultados vão para `data/`.

## 1. Conferir os dados antes de tudo

```bash
python manage.py load_cvm_data --force          # recarrega com a correção de escala da CVM
python manage.py auditar_indicadores            # lista valores fora da curva por indicador
python manage.py relatorio_qualidade_dados      # completude, consistência e atualidade
```

## 2. Pesos AHP (tabelas da seção de pesos)

```bash
python manage.py pesos_ahp
```

## 3. Análise de sensibilidade (Jaccard)

```bash
python manage.py analise_sensibilidade
```
Gera `data/sensibilidade/<data>/`: `julgamentos.csv`, `neutralidade.csv` e
`tabela_sensibilidade.tex` (copie para o Overleaf).

## 4. Backtest walk-forward

```bash
python manage.py backtest                    # 3 perfis, 7 anos de preços (~5 anos de teste)
python manage.py backtest --anos 8 --reamostragens 5000   # versão final, mais lenta
```
Gera `data/backtest/<data>/`:

| arquivo | conteúdo |
|---|---|
| `metricas.csv` | retorno, volatilidade, Sharpe, Sortino, MDD, CVaR, turnover por perfil, custo e carteira |
| `testes_sharpe.csv` | diferença de Sharpe da combinada contra cada carteira e p-valor (Ledoit-Wolf, 2008) |
| `retornos_diarios.csv` | séries líquidas (custo 0,10%) para os gráficos |
| `tabela_backtest.tex` | tabela pronta para o Overleaf |
| `resumo.json` | período, H1/H2/H3 e comparação da seleção com e sem a camada macro |

Use as datas de `resumo.json` → `periodo` para preencher o `[PREENCHER]` da seção
"Período, Janelas e Rebalanceamento".
