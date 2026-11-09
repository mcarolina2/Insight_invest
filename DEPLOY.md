# Deploy do Insight Invest no Render

Guia para colocar a plataforma no ar para o orientador acompanhar.

## 0. Antes de começar (na sua máquina)

```bash
pip install -r requirements.txt
python manage.py migrate          # aplica as migrações novas no seu banco local
python manage.py test             # deve terminar com "OK"
python manage.py runserver        # confira o site em http://127.0.0.1:8000
```

> A migração `users.0002` converte os perfis antigos para o novo formato
> (um perfil por usuário) e preserva os perfis já atribuídos.

## 1. Enviar o código para o GitHub

O zip já vem com o histórico do git e a branch `correcoes-plataforma`
(com todos os commits das correções). Na pasta do projeto:

```bash
git checkout correcoes-plataforma
git log --oneline -12                     # confira os commits das correções
git push -u origin correcoes-plataforma
```

No GitHub, abra um Pull Request de `correcoes-plataforma` para `main`,
revise e faça o merge. O Render vai publicar a `main`.

## 2. Criar o serviço no Render

1. Em https://dashboard.render.com clique em **New > Blueprint**.
2. Escolha o repositório `Insight_invest`. O Render lê o `render.yaml` e cria:
   - o banco PostgreSQL `insight-invest-db`;
   - o serviço web `insight-invest`.
3. O Render pede três valores (seu usuário administrador):
   - `DJANGO_SUPERUSER_USERNAME`
   - `DJANGO_SUPERUSER_EMAIL`
   - `DJANGO_SUPERUSER_PASSWORD` (use uma senha forte)
4. Clique em **Apply** e espere o primeiro deploy terminar (alguns minutos).
   O build já roda `migrate`, cria o grupo Analista e o seu superusuário.

O endereço do site aparece no topo da página do serviço
(algo como `https://insight-invest.onrender.com`).

## 3. Copiar os dados do seu banco local para o Render

No Render, abra o banco `insight-invest-db` > **Connect** > copie a
**External Database URL**.

No terminal, na pasta do projeto:

**Windows (PowerShell)**
```powershell
$env:DESTINO_DATABASE_URL = "cole-aqui-a-External-Database-URL"
python manage.py copiar_dados --tamanhos     # mostra o tamanho (limite: 1 GB)
python manage.py copiar_dados                # copia
Remove-Item Env:DESTINO_DATABASE_URL
```

**Linux/macOS**
```bash
export DESTINO_DATABASE_URL="cole-aqui-a-External-Database-URL"
python manage.py copiar_dados --tamanhos
python manage.py copiar_dados
unset DESTINO_DATABASE_URL
```

- Por padrão, só os últimos 400 dias de `KpiTime` são copiados.
  Se passar de 1 GB, use `--kpitime-dias 200`.
- Usuários e senhas **não** são copiados.
- Pode rodar de novo sempre que atualizar os dados na sua máquina: ativos e
  planos são atualizados (nunca apagados) e os dados de mercado são substituídos.

## 4. Calcular as carteiras no Render

1. Entre em `https://SEU-SITE.onrender.com/admin/` com o superusuário.
2. **Configurações de perfil** > marque os 3 perfis > ação
   **Recalcular carteiras dos perfis selecionados** > **Executar**.

Repita depois de cada `copiar_dados`.

## 5. Dar acesso ao economista (opcional)

A pessoa cria a conta no site normalmente. Depois, no seu terminal:

```bash
$env:DATABASE_URL = "External Database URL do Render"    # PowerShell
python manage.py criar_analista <usuario>
```

Ou pelo painel: **Usuários** > a pessoa > marcar **Membro da equipe** e
adicionar ao grupo **Analista**.

## Limites do plano gratuito

- O site "dorme" após 15 minutos sem acesso; o primeiro acesso leva ~1 minuto.
- **O banco gratuito expira 30 dias após a criação** (com 14 dias de carência,
  depois é apagado). Crie o Blueprint perto da data da defesa ou mude o banco
  para um plano pago.
- Sem terminal (Shell): por isso a cópia de dados e o recálculo são feitos
  da sua máquina e pelo painel.
- Preços vêm do Yahoo Finance; se ele bloquear o servidor, a carteira
  interativa usa a alocação por escore e mostra um aviso.
