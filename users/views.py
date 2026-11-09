from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render

from .models import PerfilRisco, RespostaQuiz, perfil_do_usuario
from .suitability import DIMENSOES, PERGUNTAS, classificar

PERFIS_VALIDOS = ("conservador", "intermediario", "arrojado")


class RegistroForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        from .models import User
        model = User
        fields = ("username",)


def perfil_obrigatorio(view):
    """Redireciona para o quiz quem ainda não tem perfil definido."""
    @wraps(view)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if perfil_do_usuario(request.user) is None:
            messages.info(request, "Responda o questionário para liberar sua carteira.")
            return redirect("users:quiz")
        return view(request, *args, **kwargs)
    return _wrapped


def _depois_do_login(user):
    return redirect("users:quiz") if perfil_do_usuario(user) is None else redirect("scoring:carteira")


def _guardar_perfil_sugerido(request):
    """A home envia ?perfil_sugerido=... ao clicar em 'Personalizar minha carteira'."""
    sugerido = request.GET.get("perfil_sugerido")
    if sugerido in PERFIS_VALIDOS:
        request.session["perfil_sugerido"] = sugerido


def registro(request):
    _guardar_perfil_sugerido(request)
    if request.user.is_authenticated:
        return _depois_do_login(request.user)
    form = RegistroForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.save())
        return redirect("users:quiz")
    return render(request, "users/registro.html", {
        "form": form, "perfil_sugerido": request.session.get("perfil_sugerido")})


def login_view(request):
    _guardar_perfil_sugerido(request)
    if request.user.is_authenticated:
        return _depois_do_login(request.user)

    erro = None
    if request.method == "POST":
        user = authenticate(request,
                            username=request.POST.get("username", "").strip(),
                            password=request.POST.get("password", ""))
        if user:
            login(request, user)
            return _depois_do_login(user)
        erro = "Usuário ou senha incorretos."

    return render(request, "users/login.html", {
        "erro": erro,
        "perfil_sugerido": request.session.get("perfil_sugerido"),
    })


def logout_view(request):
    logout(request)
    return redirect("users:login")


def _perguntas_com_dimensao():
    return [{**p, "nome_dimensao": DIMENSOES[p["dimensao"]]} for p in PERGUNTAS]


def _regras_dos_perfis():
    """Configuração em uso de cada perfil, formatada para exibição."""
    from scoring.services.motor_scoring_v2 import obter_perfis
    nomes = dict(PerfilRisco.TIPOS)
    saida = []
    for chave, cfg in obter_perfis().items():
        pesos = cfg["pesos"]
        total = sum(pesos.values()) or 1
        dims = cfg["micro_pesos"]
        filtros = []
        f = cfg.get("filtros_kpi", {})
        if "liquidez_corrente__gte" in f:
            filtros.append(f"liquidez corrente ≥ {f['liquidez_corrente__gte']:g}")
        if "divida_ebitda__lte" in f:
            filtros.append(f"dívida líquida/EBITDA ≤ {f['divida_ebitda__lte']:g}")
        if "roe__gte" in f:
            filtros.append(f"ROE ≥ {f['roe__gte'] * 100:g}%")
        saida.append({
            "chave": chave, "nome": nomes.get(chave, chave.title()),
            "descricao": cfg.get("descricao", ""),
            "qtd_ativos": cfg["qtd_ativos"], "max_pct": round(cfg["max_pct_ativo"] * 100),
            "score_min": cfg["score_min"], "filtros": filtros,
            "camadas": [("Fundamentalista", round(pesos["micro"] / total * 100)),
                        ("Macroeconômica", round(pesos["macro"] / total * 100)),
                        ("Estatística", round(pesos["estat"] / total * 100))],
            "dimensoes": [("Liquidez", round(dims["liquidez"] * 100)),
                          ("Rentabilidade", round(dims["rentabilidade"] * 100)),
                          ("Endividamento", round(dims["divida"] * 100)),
                          ("Crescimento", round(dims["crescimento"] * 100))],
        })
    return saida


def perfis_view(request):
    """Página pública que explica os três perfis com as regras em uso."""
    return render(request, "users/perfis.html", {
        "perfis": _regras_dos_perfis(),
        "meu_perfil": perfil_do_usuario(request.user),
    })


@login_required
def quiz(request):
    """Questionário de suitability (users/suitability.py). Pode ser refeito a qualquer momento."""
    if request.method == "POST":
        try:
            r = classificar({p["id"]: request.POST.get(p["id"]) for p in PERGUNTAS})
        except ValueError as e:
            return render(request, "users/quiz.html", {
                "perguntas": _perguntas_com_dimensao(), "erro": str(e), "marcadas": request.POST})
        sugerido = request.session.pop("perfil_sugerido", None) or ""
        PerfilRisco.objects.update_or_create(
            user=request.user,
            defaults={"tipo": r["perfil"], "pontuacao": r["pontos"], "respostas": r["respostas"]},
        )
        RespostaQuiz.objects.create(
            user=request.user, perfil=r["perfil"], pontuacao=r["pontos"], respostas=r["respostas"],
            por_dimensao=r["por_dimensao"], vetos=r["vetos"], perfil_sugerido_home=sugerido)
        regras = next((p for p in _regras_dos_perfis() if p["chave"] == r["perfil"]), None)
        return render(request, "users/resultado_quiz.html", {
            "resultado": r, "regras": regras,
            "perfil_sugerido": sugerido,
            "bateu_sugestao": sugerido == r["perfil"] if sugerido else None,
        })

    anteriores = {}
    obj = PerfilRisco.objects.filter(user=request.user).first()
    if obj:
        anteriores = {k: str(v) for k, v in obj.respostas.items()}
    return render(request, "users/quiz.html", {
        "perguntas": _perguntas_com_dimensao(), "marcadas": anteriores, "refazendo": bool(obj)})


@perfil_obrigatorio
def perfil_view(request):
    obj = PerfilRisco.objects.get(user=request.user)
    respostas = [
        {"pergunta": p["texto"], "resposta": p["opcoes"][obj.respostas[p["id"]]]}
        for p in PERGUNTAS if p["id"] in obj.respostas
    ]
    historico = RespostaQuiz.objects.filter(user=request.user)[:10]
    return render(request, "users/perfil.html", {"perfil": obj, "respostas": respostas,
                                                 "historico": historico})
