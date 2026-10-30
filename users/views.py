from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from functools import wraps
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render
from .models import PerfilRisco, perfil_do_usuario
from .suitability import PERGUNTAS, classificar


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


def registro(request):
    form = UserCreationForm(request.POST or None)
    if form.is_valid():
        login(request, form.save())
        return redirect("users:quiz")
    return render(request, "users/registro.html", {"form": form})


@login_required
def quiz(request):
    if request.method == "POST":
        try:
            r = classificar({p["id"]: request.POST.get(p["id"]) for p in PERGUNTAS})
        except ValueError as e:
            return render(request, "users/quiz.html",
                          {"perguntas": PERGUNTAS, "erro": str(e),
                           "marcadas": request.POST})
        PerfilRisco.objects.update_or_create(
            user=request.user,
            defaults={"tipo": r["perfil"], "pontuacao": r["pontos"],
                      "respostas": r["respostas"]},
        )
        return render(request, "users/resultado.html", {"resultado": r})
    return render(request, "users/quiz.html", {"perguntas": PERGUNTAS})


def _precisa_quiz(user):
    return user.perfil_risco is None


def _redirecionar_pos_login(user, request=None):
    if _precisa_quiz(user):
        return redirect('users:quiz')
    return redirect('carteira')


def login_view(request):
    if request.user.is_authenticated:
        return _redirecionar_pos_login(request.user, request)

    # Guarda o perfil sugerido vindo da carteira coringa (?perfil_sugerido=...)
    perfil_sugerido = request.GET.get('perfil_sugerido')
    if perfil_sugerido in ('conservador', 'intermediario', 'arrojado'):
        request.session['perfil_sugerido'] = perfil_sugerido

    erro = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return _redirecionar_pos_login(user, request)
        erro = 'Usuário ou senha incorretos.'

    return render(request, 'users/login.html', {
        'erro': erro,
        'perfil_sugerido': request.session.get('perfil_sugerido'),
    })


def logout_view(request):
    logout(request)
    return redirect('users:login')


@login_required
def quiz_view(request):
    if not _precisa_quiz(request.user):
        return redirect('carteira')

    from .models import Pergunta
    perguntas = Pergunta.objects.filter(ativa=True).prefetch_related('opcoes')

    return render(request, 'users/quiz.html', {
        'perguntas': perguntas,
        'perfil_sugerido': request.session.get('perfil_sugerido'),
    })


@login_required
def processar_quiz(request):
    if request.method != 'POST':
        return redirect('users:quiz')

    from .models import Pergunta, OpcaoResposta, RespostaUsuario, PerfilRisco
    user      = request.user
    perguntas = Pergunta.objects.filter(ativa=True).prefetch_related('opcoes')

    RespostaUsuario.objects.filter(user=user).delete()

    score_total = 0
    batch = []
    for pergunta in perguntas:
        opcao_id = request.POST.get(f'pergunta_{pergunta.id}')
        if not opcao_id:
            continue
        try:
            opcao = OpcaoResposta.objects.get(id=opcao_id, pergunta=pergunta)
        except OpcaoResposta.DoesNotExist:
            continue
        pontos = opcao.valor_score * pergunta.peso
        score_total += pontos
        batch.append(RespostaUsuario(user=user, pergunta=pergunta, opcao=opcao, score=pontos))

    RespostaUsuario.objects.bulk_create(batch)

    perfil = PerfilRisco.objects.filter(
        score_min__lte=score_total, score_max__gte=score_total
    ).first()
    if not perfil:
        perfil = PerfilRisco.objects.filter(tipo='intermediario').first()

    user.perfil_risco = perfil
    user.save(update_fields=['perfil_risco'])

    # Verifica se bateu com o perfil sugerido na carteira coringa
    perfil_sugerido = request.session.pop('perfil_sugerido', None)
    request.session['perfil_bateu_sugestao'] = (perfil_sugerido == perfil.tipo)

    return redirect('users:resultado_quiz')


@login_required
def resultado_quiz(request):
    if not request.user.perfil_risco:
        return redirect('users:quiz')

    return render(request, 'users/resultado_quiz.html', {
        'perfil':  request.user.perfil_risco,
        'usuario': request.user,
        'bateu_sugestao': request.session.pop('perfil_bateu_sugestao', False),
    })


@login_required
def perfil_view(request):
    if _precisa_quiz(request.user):
        return redirect('users:quiz')
    return render(request, 'users/perfil.html', {
        'usuario': request.user,
        'perfil':  request.user.perfil_risco,
    })