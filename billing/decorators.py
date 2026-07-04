"""
Decorators para proteger views que exigem plano Pro.
Uso:

  @requer_pro
  def minha_view(request):
      ...

  @requer_pro(redirecionar_para='billing:upgrade')
  def outra_view(request):
      ...
"""
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.contrib import messages


def requer_pro(view_func=None, *, redirecionar_para='billing:upgrade'):
    """
    Bloqueia o acesso a usuários que não têm assinatura Pro ativa.
    Funciona tanto como @requer_pro quanto @requer_pro(redirecionar_para=...).
    """
    def decorator(func):
        @wraps(func)
        @login_required
        def wrapper(request, *args, **kwargs):
            assinatura = getattr(request.user, 'assinatura', None)

            if not assinatura or not assinatura.is_pro:
                messages.info(
                    request,
                    "Esse recurso é exclusivo do plano Pro. Faça upgrade para desbloquear."
                )
                return redirect(redirecionar_para)

            return func(request, *args, **kwargs)
        return wrapper

    # Permite usar @requer_pro direto (sem parênteses) ou @requer_pro(...)
    if view_func is not None:
        return decorator(view_func)
    return decorator


def limite_recalculos(view_func):
    """
    Verifica se o usuário ainda tem recálculos disponíveis no mês.
    Aplica-se tanto a free (1x/mês) quanto a planos com limite definido.
    Pro com limite_recalculos_mes=None passa direto (ilimitado).
    """
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        assinatura = getattr(request.user, 'assinatura', None)

        if assinatura and not assinatura.pode_recalcular():
            messages.warning(
                request,
                f"Você já usou todos os recálculos disponíveis este mês "
                f"no plano {assinatura.plano.nome_exibicao}. "
                f"Faça upgrade para recalcular sem limites."
            )
            return redirect('billing:upgrade')

        return view_func(request, *args, **kwargs)
    return wrapper