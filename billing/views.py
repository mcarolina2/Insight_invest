from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.contrib.admin.views.decorators import staff_member_required
 
 
@login_required
def upgrade_view(request):
    """
    Página de upgrade — mostra o comparativo Free vs Pro e o Pix para pagar.
    """
    from .models import Plano, Assinatura
 
    plano_free = Plano.objects.filter(tipo='free').first()
    plano_pro  = Plano.objects.filter(tipo='pro').first()
    assinatura = getattr(request.user, 'assinatura', None)
 
    return render(request, 'billing/upgrade.html', {
        'plano_free':  plano_free,
        'plano_pro':   plano_pro,
        'assinatura':  assinatura,
        'ja_e_pro':    assinatura.is_pro if assinatura else False,
        # Troque pelo seu Pix real ou link de pagamento
        'pix_chave':   'seu-email@orbisdata.com.br',
        'preco_simbolico': '9,90',
    })
 
 
@login_required
def solicitar_upgrade(request):
    """
    Usuário confirma que vai pagar — cria registro 'pendente'
    e mostra instruções. Você confirma manualmente depois.
    """
    if request.method == 'POST':
        from .models import Assinatura
 
        assinatura = request.user.assinatura
        assinatura.status = 'pendente'
        assinatura.save(update_fields=['status'])
 
        messages.success(
            request,
            "Pedido registrado! Envie o comprovante do Pix para liberarmos "
            "seu acesso Pro em até 1 hora."
        )
        return redirect('billing:upgrade')
 
    return redirect('billing:upgrade')
 
 
# ---------------------------------------------------------------------------
# Confirmação manual (você usa isso enquanto valida o MVP)
# ---------------------------------------------------------------------------
 
@staff_member_required
def confirmar_pagamento_manual(request, user_id):
    """
    View simples para você (admin) confirmar o pagamento de um usuário
    com 1 clique, sem precisar abrir o Django admin.
 
    Acesse: /billing/confirmar/<user_id>/
    """
    from users.models import User
 
    user = User.objects.get(id=user_id)
    assinatura = user.assinatura
    assinatura.ativar_pro(dias=30, pagamento_id=f"manual-{user_id}", valor=9.90)
 
    messages.success(request, f"Pro ativado para {user.username} por 30 dias!")
    return redirect('/admin/billing/assinatura/')
 
 
# ---------------------------------------------------------------------------
# STUB: Mercado Pago (use quando for automatizar)
# ---------------------------------------------------------------------------
"""
Quando quiser automatizar o pagamento via Pix/cartão:
 
  pip install mercadopago
 
  import mercadopago
  sdk = mercadopago.SDK("SEU_ACCESS_TOKEN")
 
  def criar_pagamento(request):
      preference_data = {
          "items": [{
              "title": "Insight Invest Pro — 1 mês",
              "quantity": 1,
              "unit_price": 9.90,
          }],
          "payer": {"email": request.user.email},
          "back_urls": {
              "success": "https://seusite.com/billing/sucesso/",
              "failure": "https://seusite.com/billing/falha/",
          },
          "notification_url": "https://seusite.com/billing/webhook/",
      }
      preference = sdk.preference().create(preference_data)
      return redirect(preference["response"]["init_point"])
 
  @csrf_exempt
  def webhook_mercadopago(request):
      # Mercado Pago chama essa URL quando o pagamento é confirmado
      payment_id = request.GET.get("data.id")
      payment = sdk.payment().get(payment_id)
      if payment["response"]["status"] == "approved":
          user_id = payment["response"]["external_reference"]
          user = User.objects.get(id=user_id)
          user.assinatura.ativar_pro(dias=30, pagamento_id=payment_id,
                                       valor=payment["response"]["transaction_amount"])
      return JsonResponse({"ok": True})
"""
 