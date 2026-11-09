"""
Corrige os tickers da tabela Ativo usando o cadastro oficial da CVM (FCA).

Problema encontrado: quando a API da B3 não respondia, uma versão antiga do
extrator criava o ticker a partir das 4 primeiras letras do nome da empresa +
"3" (ITAU3, WEG3, AMBE3, JBS3, TIM3...). Esses códigos não existem na bolsa,
então não há preço no Yahoo; e, como o CNPJ era único por ativo, o ticker
verdadeiro (ITUB3/ITUB4, WEGE3...) ficava sem CNPJ e sem dados da CVM.

    python manage.py corrigir_tickers                 # só mostra o diagnóstico
    python manage.py corrigir_tickers --aplicar       # aplica as correções
    python manage.py corrigir_tickers --arquivo fca_cia_aberta_2026.zip   # sem baixar

O que --aplicar faz:
  * ticker verdadeiro com CNPJ ausente/errado  -> recebe o CNPJ certo;
  * ticker inventado de empresa listada        -> é removido (com seus KPIs e
    escores) e os tickers verdadeiros da empresa são criados, se faltarem;
  * empresa sem ação negociada na bolsa        -> marcada como inativa
    (fica fora do ranking e das carteiras).
Depois rode: load_cvm_data --force, load_estatistico, calcular_scores e
atualizar_carteiras.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from services.extractors.fca_extractor import so_digitos, tickers_oficiais


def _tipo(ticker: str) -> str:
    return "acao"


class Command(BaseCommand):
    help = "Corrige tickers inventados e CNPJs usando o Formulário Cadastral (FCA) da CVM."

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true", help="grava as correções (padrão: só diagnóstico)")
        parser.add_argument("--arquivo", default=None, help="ZIP ou CSV do FCA já baixado")
        parser.add_argument("--mostrar", type=int, default=25, help="quantos exemplos listar por grupo")

    def handle(self, *args, aplicar, arquivo, mostrar, **o):
        from portfolio.models import Ativo

        try:
            fca = tickers_oficiais(arquivo=arquivo)
        except Exception as e:
            raise CommandError(f"Não foi possível ler o FCA da CVM: {e}")
        self.stdout.write(f"FCA: {len(fca)} tickers negociados de {fca['cnpj_digitos'].nunique()} companhias")

        cnpj_do_ticker = dict(zip(fca["ticker"], fca["cnpj"]))
        tickers_da_empresa = defaultdict(list)
        for t, d in zip(fca["ticker"], fca["cnpj_digitos"]):
            tickers_da_empresa[d].append(t)
        cnpj_formatado = dict(zip(fca["cnpj_digitos"], fca["cnpj"]))

        existentes = {a.ticker: a for a in Ativo.objects.all()}
        g = {"ok": [], "cnpj_corrigido": [], "inventado": [], "sem_negociacao": [], "fora": []}
        criar = {}                                   # ticker -> modelo de dados (Ativo inventado)

        for a in existentes.values():
            dig = so_digitos(a.cnpj)
            if a.ticker in cnpj_do_ticker:
                if so_digitos(cnpj_do_ticker[a.ticker]) != dig:
                    g["cnpj_corrigido"].append(a)
                else:
                    g["ok"].append(a)
                for t in tickers_da_empresa[so_digitos(cnpj_do_ticker[a.ticker])]:
                    if t not in existentes:
                        criar.setdefault(t, a)
            elif dig and dig in tickers_da_empresa:
                g["inventado"].append(a)
                for t in tickers_da_empresa[dig]:
                    if t not in existentes:
                        criar.setdefault(t, a)
            elif dig:
                g["sem_negociacao"].append(a)
            else:
                g["fora"].append(a)              # sem CNPJ e fora do FCA (FII, BDR, ETF...)

        def amostra(lista, fmt):
            itens = [fmt(x) for x in lista[:mostrar]]
            return ", ".join(itens) + (" ..." if len(lista) > mostrar else "")

        self.stdout.write(self.style.MIGRATE_HEADING("\nDiagnóstico"))
        self.stdout.write(f"  Tickers corretos                         : {len(g['ok'])}")
        self.stdout.write(f"  Tickers corretos com CNPJ ausente/errado : {len(g['cnpj_corrigido'])}")
        if g["cnpj_corrigido"]:
            self.stdout.write("     " + amostra(g["cnpj_corrigido"], lambda a: a.ticker))
        self.stdout.write(f"  Tickers inventados (não existem na B3)   : {len(g['inventado'])}")
        if g["inventado"]:
            self.stdout.write("     " + amostra(g["inventado"], lambda a: (
                f"{a.ticker}→{'/'.join(tickers_da_empresa[so_digitos(a.cnpj)])}")))
        self.stdout.write(f"  Tickers verdadeiros a criar              : {len(criar)}")
        self.stdout.write(f"  Empresas sem ação negociada em bolsa     : {len(g['sem_negociacao'])}")
        if g["sem_negociacao"]:
            self.stdout.write("     " + amostra(g["sem_negociacao"], lambda a: f"{a.ticker} ({a.nome[:25]})"))
        self.stdout.write(f"  Sem CNPJ e fora do FCA (FII/BDR/ETF etc.): {len(g['fora'])}")

        # posições e recomendações de usuários não são apagadas automaticamente
        rel_usuario = [r for r in Ativo._meta.related_objects
                       if r.related_model.__name__ in ("Posicao", "ItemRecomendacao")]
        com_carteira = [a for a in g["inventado"]
                        if any(r.related_model.objects.filter(**{r.field.name: a}).exists() for r in rel_usuario)]
        if com_carteira:
            self.stdout.write(self.style.WARNING(
                f"  {len(com_carteira)} tickers inventados estão em carteiras de usuários e não serão removidos: "
                + ", ".join(a.ticker for a in com_carteira)))

        if not aplicar:
            self.stdout.write(self.style.WARNING("\nNada foi alterado. Rode com --aplicar para gravar."))
            return

        with transaction.atomic():
            for a in g["cnpj_corrigido"]:
                a.cnpj = cnpj_do_ticker[a.ticker]
                a.ativo = True
                a.save(update_fields=["cnpj", "ativo", "atualizado_em"])
            for t, modelo in criar.items():
                Ativo.objects.create(ticker=t, nome=modelo.nome, setor=modelo.setor, subsetor=modelo.subsetor,
                                     tipo=_tipo(t), cnpj=cnpj_do_ticker[t], ativo=True)
            removidos = 0
            for a in g["inventado"]:
                if a in com_carteira:
                    a.ativo = False
                    a.save(update_fields=["ativo", "atualizado_em"])
                else:
                    a.delete()
                    removidos += 1
            Ativo.objects.filter(id__in=[a.id for a in g["sem_negociacao"]]).update(ativo=False)

        self.stdout.write(self.style.SUCCESS(
            f"\nCorrigido: {len(g['cnpj_corrigido'])} CNPJs, {len(criar)} tickers criados, "
            f"{removidos} inventados removidos, {len(g['sem_negociacao'])} empresas inativadas."))
        self.stdout.write("Agora rode: load_cvm_data --force, load_estatistico, calcular_scores, atualizar_carteiras")
