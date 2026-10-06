# CryptoBR Social Intelligence

Protótipo local e reproduzível para o desafio técnico. O plano em
[`Plano_CryptoBR_Social_Intelligence.md`](Plano_CryptoBR_Social_Intelligence.md)
é a fonte de verdade para escopo, prioridades e decisões de produto.

## Estado atual

O primeiro incremento cobre a base de ingestão:

- dataset JSONL inteiramente sintético e determinístico;
- coleta por páginas/cursor, não por leitura direta como se fosse paginação;
- checkpoint em SQLite gravado na mesma transação que os posts;
- identidade única por `(platform, post_id)` para reexecução idempotente;
- retry limitado para falha transitória simulada e registro de execuções.
- associação lexical explicável de posts a eventos, com IDs estáveis;
- classificação em, no máximo, cinco categorias configuradas;
- janelas de quinze minutos, baseline de três janelas e score de tendência
  com componentes, HHI, estágio e ranking persistidos.
- cenários sintéticos para cópias, rumor de fonte única, negação, evento antigo
  recirculado, incidente/listagem separados e conteúdo de prompt injection;
- critic determinístico que limita destaques sem referência válida e mantém
  rumores de origem única em monitoramento.
- console administrativo Streamlit com filtro por categoria, ranking, métricas,
  incertezas/riscos e histórico persistente de revisão humana.

O agente opcional, os alertas/revisões persistentes e o painel administrativo
estão implementados. A conexão SSE do Mastodon permanece opcional: em
`mastodon.social` a política da instância recusou tanto token inválido quanto o
stream público durante a validação; a timeline REST pública por hashtag foi
validada e é tratada como uma amostra limitada, não como cobertura global.

## Executar

Requer Python 3.11+ para a entrega final. A base atual também é compatível com
Python 3.9 do ambiente local, para permitir validação antes da atualização do
runtime.

```bash
python3 scripts/generate_dataset.py --seed 42 --as-of 2026-10-06T15:00:00Z --batch initial
python3 -m app.cli collect --simulate-transient-error
python3 -m app.cli analyze --as-of 2026-10-06T14:45:00Z
python3 scripts/generate_dataset.py --seed 42 --as-of 2026-10-06T15:00:00Z --batch update --append
python3 -m app.cli collect --reopen-exhausted
OPENROUTER_API_KEY='...' python3 -m app.cli analyze --as-of 2026-10-06T15:00:00Z --with-agent
# Painel da demonstração sintética (somente para o roteiro reproduzível):
CRYPTOBR_DATABASE=data/cryptobr.sqlite3 streamlit run dashboard.py
python3 -m unittest discover -s tests -v
```

Para demonstrar o modo contínuo local sem incluir uma janela parcial no score,
use o polling abaixo. `--max-cycles` torna a execução finita para a demo; sem
ele, interrompa com `Ctrl+C`.

```bash
.venv/bin/python app/cli.py watch --interval-seconds 60
.venv/bin/python app/cli.py watch --interval-seconds 1 --max-cycles 1
```

A segunda execução de `collect` reporta `already_exhausted` e preserva a mesma
contagem de posts. Os arquivos gerados ficam em `data/` e não são versionados.

O comando `--reopen-exhausted` é reservado ao polling da mesma fonte depois de
ela receber registros anexados: retoma da posição de cursor persistida e não
reconta os posts anteriores. A coleta registra no SQLite a cobertura declarada
pela fonte, exibida no painel.

### Fonte externa opcional: Mastodon

O conector usa somente a API permitida de uma instância e uma hashtag; ele não
representa cobertura global. Defina `MASTODON_BASE_URL` e, se a instância exigir,
`MASTODON_TOKEN`, então execute:

```bash
python3 -m app.cli collect --source mastodon --hashtag crypto --max-pages 10
```

Para uma timeline pública, `--public` não envia o token configurado e evita que
uma credencial inválida bloqueie a fonte:

```bash
.venv/bin/python app/cli.py collect --source mastodon --public --hashtag bitcoin --max-pages 1
```

O painel abre por padrão o banco dessa coleta externa (sem misturá-lo ao
dataset de demonstração). Para indicar o caminho explicitamente, execute:

```bash
CRYPTOBR_DATABASE=data/mastodon_public.sqlite3 .venv/bin/streamlit run dashboard.py
```

Posts externos são exibidos como cobertura limitada da consulta/instância. O
primeiro agrupamento fora do dataset é deliberadamente estreito (`Bitcoin —
discussão pública observada`) e heurístico; não é uma confirmação de evento.

No macOS, `start_dashboard.command` abre o painel por duplo clique. Dentro do
painel, informe uma hashtag e clique em **Buscar agora**; não é necessário
montar comandos de coleta no terminal.

Erros `401` e `403` param a coleta para correção de acesso; `429`, `5xx` e erros
de transporte usam o retry limitado. Não há scraping HTML nem tentativa de
contornar login, rate limits ou políticas da instância.

Por padrão, o Mastodon é limitado a dez páginas por rodada e retorna
`partial_page_limit` com checkpoint preservado quando há mais dados. A rodada
seguinte retoma do cursor salvo; o feed local não recebe esse limite por padrão.

### Streaming de hashtag

Para eventos sociais novos em tempo real, use um token de usuário com
`read:statuses`. A execução é deliberadamente limitada para a demo e reabre a
conexão até três vezes para falhas transitórias:

```bash
export MASTODON_BASE_URL='https://sua-instancia.social'
export MASTODON_TOKEN='...'
python3 -m app.cli stream --hashtag bitcoin --max-events 20
```

Alternativamente, use o arquivo `.env` local (já ignorado pelo Git):

```dotenv
MASTODON_BASE_URL=https://mastodon.social
MASTODON_TOKEN=seu_token_de_usuario
```

O stream captura apenas novos posts que a instância conhece durante a conexão;
não é uma busca histórica nem cobertura global.

Se a instância permitir streams públicos, o OAuth pode ser dispensado para a
demo. Esse modo não envia `MASTODON_TOKEN` (inclusive se ele estiver no `.env`):

```bash
.venv/bin/python app/cli.py stream --public --hashtag bitcoin --max-events 5
```

Antes do stream, valide a credencial sem expor seu token:

```bash
python -m app.cli mastodon-doctor
```

Erros esperados de token, escopo ou rede retornam JSON legível, sem traceback.

Se você tiver `MASTODON_CLIENT_ID`, `MASTODON_CLIENT_SECRET` e um código de
autorização de usuário, salve-os temporariamente no `.env` junto de
`MASTODON_REDIRECT_URI=urn:ietf:wg:oauth:2.0:oob` e execute:

```bash
python -m app.cli mastodon-authorize
```

O comando troca o código (uso único), grava o token de usuário em `.env` com
permissão local restrita e remove o código de autorização. Nunca imprime o
token ou o segredo do cliente.

## Limites atuais

O conector é local, identificado como sintético, e não representa cobertura de
redes sociais. Não há chamadas a LLM, fontes externas ou inferências de fatos
reais nesta etapa.

## Agente opcional

Defina `OPENROUTER_API_KEY` no ambiente e, opcionalmente,
`OPENROUTER_MODEL=openrouter/free`. O agente tem no máximo quatro chamadas de
ferramenta e pode consultar somente métricas e até oito evidências locais do
tópico sob análise. Sem chave, ele gera uma sugestão explicitamente marcada
como `simulated`; falhas do provedor ficam marcadas como `unavailable`.

## Revisão humana

O painel permite aprovar, rejeitar ou editar uma sugestão sem apagar a saída
automática. Também é possível registrar uma decisão pelo CLI:

```bash
python3 -m app.cli review --alert-id 1 --decision EDIT --reviewer ana --summary "Resumo revisado"
```

As expectativas do dataset ficam separadas em
[`config/scenario_expectations.json`](config/scenario_expectations.json), para
que os rótulos de cenário nunca sejam entrada do agrupador.
