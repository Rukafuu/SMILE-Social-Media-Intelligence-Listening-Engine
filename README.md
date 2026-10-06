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

O agente, alertas/revisões e painel administrativo ainda serão implementados
nos próximos incrementos.

## Executar

Requer Python 3.11+ para a entrega final. A base atual também é compatível com
Python 3.9 do ambiente local, para permitir validação antes da atualização do
runtime.

```bash
python3 scripts/generate_dataset.py --seed 42 --as-of 2026-10-06T15:00:00Z
python3 -m app.cli collect --simulate-transient-error
python3 -m app.cli analyze --as-of 2026-10-06T15:00:00Z
OPENROUTER_API_KEY='...' python3 -m app.cli analyze --as-of 2026-10-06T15:00:00Z --with-agent
streamlit run dashboard.py
python3 -m unittest discover -s tests -v
```

A segunda execução de `collect` reporta `already_exhausted` e preserva a mesma
contagem de posts. Os arquivos gerados ficam em `data/` e não são versionados.

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
