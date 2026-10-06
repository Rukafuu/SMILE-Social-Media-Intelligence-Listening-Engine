# Validação da entrega SMILE

Esta validação corresponde à implementação com cobertura declarada, snapshots e
contrato de agente v2. Os exemplos em [`examples/results.json`](examples/results.json)
foram produzidos pelo próprio pipeline, com seed 42 e dados inteiramente sintéticos.

## Execução verificada

Ambiente desta rodada: Python 3.12.14, SQLite local e Streamlit 1.65.0, em Linux.
**60 testes passaram**, incluindo quatro testes do painel usando `AppTest`.
Os conectores e o LLM foram testados com transportes controlados, sem chamadas a
provedores reais nesta rodada. Não foi executado o atalho macOS em um Mac.

```bash
python -m unittest discover -s tests -v
python scripts/run_demo.py --output data/demo-results.json
```

A demo trabalha em banco temporário por padrão. Seu resultado tem 416 posts iniciais,
345 no update, 761 antes da confirmação e 762 depois. A coleta inicial simula uma
falha na segunda página e conclui com uma repetição limitada; a reexecução não insere
posts. Dez tópicos têm métricas; o placar esportivo sem contexto financeiro fica fora.

## Matriz dos requisitos

| Requisito do teste | Evidência executável |
|---|---|
| Fonte executável e normalização | JSONL paginado; testes de coleta, Mastodon, modelos e ausência de engajamento |
| Deduplicação, retry e checkpoint | `test_collection`, `test_page_limit`, verificação do prefixo e URI canônica |
| Ranking além de volume | `test_eight_scenarios_and_changed_ranking`; ranking capturado abaixo |
| Agrupar paráfrases / separar eventos | `test_real_paraphrases_share_event_but_different_actions_do_not` |
| Pouco histórico | Testes do pipeline com um post e sem manifesto; score/baseline ficam `null` |
| Agente com consulta e saída estruturada | Ambos os tools exigidos; schema de seis campos; sucesso controlado e orçamento testados |
| Evidências e incerteza | Referência inventada/não consultada, confirmação falsa e afirmação fabricada são limitadas |
| Prompt injection | Post entra na amostra; mensagem da ferramenta contém sua instrução; `shell` é recusado |
| Circulação | HHI, famílias, vínculos conhecidos e cobertura; independência/região desconhecidas |
| Repositório / admin | SQLite; AppTest aprova, edita e rejeita; saída automática e revisão separadas |
| Atualizações | Snapshot antigo permanece imutável; nova evidência/versão cria alerta pendente |
| Arquitetura, escala e custos | `ARCHITECTURE.md`: hipóteses explícitas, gargalos locais e evolução |
| Reprodução e documentação | Gerador, `run_demo.py`, expectativas separadas e saída JSON versionada |

## Ranking observado

Janela `[2026-10-06T14:45:00Z,2026-10-06T15:00:00Z)`, antes da confirmação.
A ordenação de empates é determinística; os dois rankings comparam os mesmos tópicos.

| Tópico | Posts | Rank volume | Rank score | Score | Recomendação offline |
|---|---:|---:|---:|---:|---|
| Token Aurora — protocolo | 24 | 3 | 1 | 94,00 | MONITOR |
| Exchange Aurora — listagem | 20 | 5 | 2 | 87,87 | MONITOR |
| Regulador — ETF | 10 | 10 | 3 | 68,08 | MONITOR |
| Mercado — juros | 10 | 9 | 4 | 68,08 | MONITOR |
| Horizonte — patrocínio | 10 | 8 | 5 | 68,08 | MONITOR |
| Bitcoin — ETFs | 101 | 2 | 6 | 49,99 | MONITOR |
| NovaChain — auditoria | 21 | 4 | 7 | 41,95 | MONITOR |
| Exchange Aurora — incidente | 16 | 6 | 8 | 38,96 | MONITOR |
| Orbit — anúncio antigo | 12 | 7 | 9 | 32,78 | DISCARD |
| Pumplet — cópias | 120 | 1 | 10 | 3,74 | DISCARD |

Aurora cresce de baseline 2 para 24 contribuições diversas. Bitcoin se mantém perto
de baseline 100; seu volume alto não indica crescimento. Pumplet tem muita repetição
e apenas dois autores, reduzindo suporte e aumentando as penalidades. Nenhum desses
resultados é diagnóstico de bots ou confirmação de fatos.

O modo determinístico conserva sugestões em acompanhamento quando o sinal é alto.
Um teste de LLM controlado demonstra HIGHLIGHT para Aurora com ferramentas consultadas,
referências corretas e trechos literais. Isso testa o contrato, não um provedor real.

## Regressões corrigidas

1. **Histórico ausente como zero:** o caminho real `collect → analyze` agora exige
   cobertura válida. Teste com um post retorna `insufficient_history`, sem score.
   Outro teste prova que baseline zero é válido quando a janela vazia está coberta.
2. **Resposta sem ferramentas:** duas respostas finais sem consulta falham no contrato;
   ocorre fallback explícito. Referências existentes mas não consultadas também falham.
3. **Evidência inventada reparada automaticamente:** não há preenchimento de referências.
   O ID inválido é sinalizado/removido; afirmação fica sem suporte e não ganha destaque.
4. **“Lucro confirmado de 999%”:** teste mantém esse texto apenas como interpretação
   não verificada, rebaixa a claim e usa resumo principal das métricas observadas.
5. **Post malicioso excluído do agrupamento:** agora pertence ao tópico Bitcoin/ETF,
   é priorizado e aparece como dado em `get_topic_evidence`. Ferramenta indevida,
   troca de tópico e extrapolação do orçamento são rejeitadas pelo host.
6. **Evidências futuras em alertas antigos:** snapshot vinculado a janela/versão;
   teste coleta um post posterior e verifica que a evidência antiga não muda.
7. **Alerta antigo com métricas novas:** join do painel exige mesma janela e versão.
   Reanálise não altera revisões antigas e não reutiliza aprovação em outro alerta.
8. **Demo sem diversidade dos casos:** cópias, paráfrases reais, ações distintas,
   contradição, evento antigo e autoridade sintética têm demonstrações próprias.

Há ainda testes para migração aditiva/repetida de bancos anteriores, retry de LLM sem
apagar a revisão, cache sem novas chamadas, prefixo alterado, rejeições que invalidam
cobertura, timestamps fracionários, fonte mista e espera longa em `Retry-After`.

## Limites da validação

A demonstração é sintética. Passar nos testes comprova esses cenários e contratos;
não mede recall/precision em redes reais nem robustez de um modelo contra todo prompt
injection. A barreira de ferramentas é executável; a interpretação de texto continua
exigindo revisão. Trechos literais não verificam a verdade das afirmações publicadas.
A escolha lexical não resolve eventos arbitrários, datas ambíguas ou cópias aproximadas.

O conector público não estabelece cobertura histórica completa. Nesta situação, o
score é indisponível; o painel mostra os posts e incertezas do recorte sem inventar
crescimento. A execução real do agente depende de chave, modelo com ferramentas e
acesso do provedor; `analysis_mode=openrouter` deve ser observado junto de `agent_calls`.

Não houve teste de carga para dez milhões de posts/dia. O desenho e os preços são
hipotéticos. Para hospedagem, autenticação, concorrência operacional, controle de
acesso e proteção de credenciais exigiriam trabalho adicional. O painel desta entrega
é local e não publica alertas automaticamente.
