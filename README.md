# SMILE — Social Media Intelligence & Listening Engine

Protótipo de inteligência social para o teste técnico da CryptoBR: coleta publicações,
agrupa eventos, calcula tendências explicáveis e cria sugestões com evidências para
revisão humana. O recorte inicial é cripto, investimentos e finanças.

A entrega inclui uma demonstração totalmente sintética e reproduzível, um conector
Mastodon opcional, um agente OpenRouter com ferramentas de leitura e um painel
Streamlit. **Score de tendência não é probabilidade de um fato ser verdadeiro.**

## Começar

Python 3.11+ recomendado. O núcleo usa a biblioteca padrão; Streamlit é a dependência
do painel, fixado na versão verificada 1.65.0. Execute os comandos na raiz deste repositório:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/run_demo.py --database data/smile_demo_v2.sqlite3 --output data/demo-results.json
SMILE_DATABASE=data/smile_demo_v2.sqlite3 python -m streamlit run dashboard.py --server.address 127.0.0.1
```

Abra `http://127.0.0.1:8501`. A demonstração não faz chamadas externas, mesmo se houver
uma chave de LLM no ambiente. Ela coleta dois lotes, simula retry, prova idempotência,
analisa dez tópicos, registra APPROVE/EDIT/REJECT e acrescenta uma confirmação oficial
**somente dentro da simulação**, criando uma nova versão em revisão pendente.

O banco indicado em `--database` precisa ser novo; o script recusa sobrescrever dados
ou revisões existentes. Para repetir, omita `--database` (usa banco temporário) ou
escolha outro nome. `CRYPTOBR_DATABASE` continua aceito como alternativa a
`SMILE_DATABASE`. Sem configuração, o painel abre `data/mastodon_public.sqlite3`.
No macOS, `start_dashboard.command` continua disponível após criar a `.venv`.

## Demonstração por etapas

Este roteiro permite inspecionar checkpoint e atualização usando um banco novo:

```bash
python scripts/generate_dataset.py --seed 42 --as-of 2026-10-06T15:00:00Z --batch initial
python -m app.cli collect --database data/smile_steps_v2.sqlite3 --simulate-transient-error
python -m app.cli collect --database data/smile_steps_v2.sqlite3
python -m app.cli analyze --database data/smile_steps_v2.sqlite3 --as-of 2026-10-06T14:45:00Z --with-agent --max-agent-topics 0
python scripts/generate_dataset.py --seed 42 --as-of 2026-10-06T15:00:00Z --batch update --append
python -m app.cli collect --database data/smile_steps_v2.sqlite3 --reopen-exhausted
python -m app.cli analyze --database data/smile_steps_v2.sqlite3 --as-of 2026-10-06T15:00:00Z --with-agent --max-agent-topics 0
```

A segunda coleta retorna `already_exhausted`, sem aumentar a contagem. O lote inicial
tem 416 registros; o update tem 345, totalizando 761. Após uma confirmação opcional,
são 762. O gerador salva um manifesto `.meta.json` com hash do feed e intervalo de
cobertura. Append só é aceito para lotes contíguos; o prefixo já coletado é verificado
antes de uma retomada. Não substitua um feed atrás de um checkpoint existente.

Para mostrar a evolução de um rumor, registre uma revisão do alerta atual no painel,
então execute:

```bash
python scripts/generate_dataset.py --confirmation --as-of 2026-10-06T15:00:00Z
python -m app.cli collect --database data/smile_steps_v2.sqlite3 --reopen-exhausted
python -m app.cli analyze --database data/smile_steps_v2.sqlite3 --as-of 2026-10-06T15:00:00Z --with-agent --max-agent-topics 0
```

O `topic_id` permanece igual. A versão da mesma janela muda; o alerta anterior e sua
revisão permanecem armazenados. Nova evidência não herda aprovação humana.

## Tendências e os oito cenários

Resultados medidos antes da confirmação, seed 42, janela `[14:45,15:00)` UTC de
06/10/2026. Exemplos completos: [`examples/results.json`](examples/results.json).

| Caso | Resultado observado |
|---|---|
| Muito volume, mas estável | Bitcoin: 101 posts, baseline 100, score 49,99, estágio `stable` |
| Tema menor crescendo com diversidade | Token Aurora: 24 posts, baseline 2, score 94,00; supera Bitcoin |
| Muitas cópias em poucas contas | Pumplet: 120 posts, dois autores, uma família; score 3,74, `DISCARD` |
| Rumor de uma origem | NovaChain: origem do rumor única, com negação separada; `MONITOR`, contradição explícita |
| Mesmo evento com redações diferentes | “Token Aurora anuncia protocolo” e “Aurora token apresenta atualização da rede” compartilham ID |
| Eventos distintos da mesma entidade | Listagem e incidente da Exchange Aurora têm IDs e evidências separados |
| Notícia antiga republicada | Orbit: data do evento sete dias anterior, sinal de recirculação, `DISCARD` |
| Instrução maliciosa | Post `synthetic-injection-001` entra no tópico Bitcoin e na amostra enviada pela ferramenta |

Pumplet ocupa o primeiro lugar por volume e o último por score. Token Aurora fica em
terceiro por volume e primeiro por score. Os rótulos de expectativa estão separados em
[`config/scenario_expectations.json`](config/scenario_expectations.json); não são
entrada do agrupador.

O método usa a última janela fechada de quinze minutos e as três anteriores. Um
horário intermediário é arredondado para a última fronteira fechada em UTC.

- `N`: posts; `n`: contribuições distintas por autor/família; `U`: autores; `F`: famílias.
- `b`: média de `n` nas três janelas anteriores, com cobertura completa.
- `G = clip(log2((n+1)/(b+1))/3, 0, 1)`; `V = min(1,n/30)`; `A = min(1,U/20)`.
- `D = 1-F/N`; `HHI = sum((posts_do_autor/N)^2)`; `support = min(1,U/5)`.
- `score = 100*(0.5G+0.2V+0.3A)*(1-0.6D)*(1-0.5HHI)*support`.

Sem cobertura das quatro janelas, score e baseline são `null`, com
`insufficient_history`. Janela vazia **coberta** pode ter zero; falta de coleta não
pode. A cobertura completa só é declarada para um fixture sintético verificado,
coletado até o fim e sem registros rejeitados. REST/SSE públicos permanecem amostras
limitadas. Engajamento disponível e sua cobertura são registrados; não entra no
score deste MVP. Ausência de views/reposts permanece `null`.

As famílias detectam cópias exatas após normalizar caixa e espaços. O agrupamento de
eventos usa entidade mais ação e alternativas lexicais. Não utiliza embeddings nem
resolve paráfrases arbitrárias; dois eventos com a mesma entidade/ação podem se
misturar. Essas limitações são explícitas, em vez de apresentar o MVP como detector
geral de eventos.

## Agente, evidências e validação

Copie `.env.example` para `.env` e configure `OPENROUTER_API_KEY` e um
`OPENROUTER_MODEL` com suporte a tool calling. As variáveis do processo prevalecem
sobre o arquivo. A execução com modelo é opcional para operar a demo, mas é a forma
de demonstrar análise de LLM real:

```bash
python -m app.cli analyze --database data/smile_steps_v2.sqlite3 --as-of 2026-10-06T15:00:00Z --with-agent --max-agent-topics 5
```

O orçamento padrão permite cinco tópicos com LLM; os demais recebem análise
`deterministic_no_llm`. O tópico com prompt injection tem prioridade para entrar
nesse orçamento. Sem chave, toda a análise é determinística e identificada como tal.
Uma falha de provedor, contrato ou limite usa `fallback_no_llm` com `llm_failed`.

O agente precisa consultar **ambas** as ferramentas: `get_topic_metrics` e
`get_topic_evidence`. Tem no máximo quatro chamadas ao modelo, seis chamadas a
ferramentas, uma tentativa de reparar o contrato, oito evidências por consulta e
1.800 caracteres por post. A rede tem timeout de 25 segundos por chamada e resposta
limitada a 256 KiB. Não recebe SQL, shell, escrita, publicação ou navegação arbitrária.

Cada evidência vem do snapshot imutável do tópico/janela/versão; posts futuros não
entram em alertas antigos. Referências usam `platform:post_id`. Elas precisam ter sido
realmente consultadas; referências inventadas são sinalizadas e removidas, **sem
anexar outros posts para justificar o texto do modelo**.

Claims observadas exigem trecho literal do post. Isso prova o conteúdo da publicação,
não a verdade do evento. Interpretações sem suporte ficam `unconfirmed`.
`confirmed_in_simulation` exige trecho de fonte `synthetic_official`, identificada
como sintética; não existe confirmação automática de fatos reais neste MVP.
O resumo principal é gerado a partir das métricas observadas. Texto livre de LLM é
preservado separadamente como `unverified_interpretation` para auditoria.

HIGHLIGHT indica sinal relevante para revisão, MONITOR indica acompanhamento e
DISCARD indica baixa prioridade. Referências ruins, afirmações sem suporte,
contradições, evento antigo, origem única e concentração limitam recomendações.
Concentração não prova manipulação. O HHI e os vínculos conhecidos de origem/repost
não comprovam independência, propagação global ou localização.

Alertas incluem janela, versão de métricas, score/componentes, estágio, evidências,
incertezas, riscos, `created_at` e `review_status=pending`. O cache é por tópico,
janela, versão, política do agente e modelo; mudanças geram nova versão. Para tentar
novamente uma falha, acrescente `--retry-agent`: a tentativa cria outro alerta e
preserva revisões anteriores. As ferramentas consultadas ficam em `agent_calls`.

## Revisão humana e categorias

O painel lista rankings por score e volume, filtra categorias, mostra evidências,
permite corrigir classificação e registrar APPROVE/EDIT/REJECT. A decisão humana e
o resumo editado ficam em tabelas próprias; nunca substituem a saída automática.
Aprovação/rejeição vale apenas para aquele alerta; EDIT mantém revisão pendente.
Também existe revisão por CLI:

```bash
python -m app.cli review --database data/smile_steps_v2.sqlite3 --alert-id 1 --decision EDIT --reviewer ana --summary "Resumo revisado"
```

A taxonomia tem cinco categorias em `config/themes.json`: Cripto e Web3,
Investimentos e Mercados, Política e Regulação, Empresas e Finanças e Esportes e
Entretenimento. Há uma principal e no máximo uma secundária, sem duplicar o tópico
no ranking global. Sem suporte lexical, a categoria é `null`, não uma sexta categoria.
Correção humana prevalece na reanálise. Esportes entram pelo contexto financeiro,
como o patrocínio cripto do cenário; placares sem esse vínculo ficam fora do recorte.

## Mastodon opcional

Defina `MASTODON_BASE_URL` como HTTPS. A coleta REST por hashtag usa a API permitida:

```bash
python -m app.cli collect --source mastodon --public --hashtag bitcoin --max-pages 1 --database data/mastodon_public.sqlite3
python -m app.cli analyze --database data/mastodon_public.sqlite3 --as-of 2026-10-06T15:00:00Z --with-agent --max-agent-topics 0
```

Para uma coleta atual, use a fronteira UTC da última janela fechada no lugar da data
fixa da demo. No painel externo, **Buscar agora** coleta uma página, recalcula as
janelas fechadas e cria sugestões determinísticas. Essa ação fica desabilitada em
bancos sintéticos. Bancos mistos são recusados pela análise para evitar comparações
entre amostras incompatíveis.

`--public` omite tokens. Instâncias que exigem login podem usar `MASTODON_TOKEN`.
401/403 interrompem para corrigir acesso; 429/5xx e falhas de transporte usam até três
tentativas. REST espera um segundo entre páginas e respeita `Retry-After`; espera
superior a trinta segundos encerra a rodada para retomada posterior, sem tentar cedo.
O checkpoint REST retoma a paginação histórica; a busca do painel inicia uma nova
consulta amostrada. IDs são separados por instância e URI canônica deduplica o mesmo
post federado. Nenhuma dessas estratégias equivale a cobertura global.

Streaming e diagnóstico de credenciais permanecem opcionais:

```bash
python -m app.cli mastodon-doctor
python -m app.cli stream --hashtag bitcoin --max-events 20 --database data/mastodon_public.sqlite3
```

A conexão usa somente novos eventos conhecidos pela instância e depende de sua
política. OAuth (`mastodon-auth-url` / `mastodon-authorize`) continua disponível;
segredos são gravados apenas no `.env` local, com permissão restrita. Não contorne
bloqueios. SSE não garante recuperar eventos perdidos durante desconexões; REST é
a opção de backfill. Nenhuma chamada real de Mastodon/LLM foi feita nesta rodada de
verificação; os testes de transporte usam respostas controladas.

Polling do feed local usa janelas fechadas:

```bash
python -m app.cli watch --database data/smile_steps_v2.sqlite3 --interval-seconds 60 --max-cycles 2
```

Use um fixture com datas compatíveis com o relógio atual para observar atualização.
O polling recalcula métricas; alertas são criados por `analyze --with-agent`.

## Validação, arquitetura e limites

```bash
python -m unittest discover -s tests -v
python scripts/run_demo.py --output data/demo-results.json
```

Veja [`VALIDATION.md`](VALIDATION.md) para testes, demonstração e limites da evidência,
e [`ARCHITECTURE.md`](ARCHITECTURE.md) para diagrama, escala de dez milhões de posts/dia,
custos hipotéticos e evolução de comunidades/narrativas/regiões. O
[`plano`](Plano_CryptoBR_Social_Intelligence.md) registra as decisões finais do MVP.

SQLite usa migrações aditivas: preserva posts, alertas e revisões existentes. Alertas
legados sem janela/versão permanecem no banco, mas não são associados a novas
métricas. Checkpoints locais legados sem hash exigem uma fonte/banco isolado para
retomada verificável. O novo gerador deve ser usado em um banco novo, como no roteiro.
Não há hospedagem obrigatória, grafo completo de comunidades, detecção geral de
paráfrases, validação semântica geral ou operação de escala implementada.
