# SMILE — decisões e escopo do MVP CryptoBR

Este documento registra as decisões finais. O estado executável e seus comandos estão
no [README](README.md); resultados em [examples/results.json](examples/results.json),
validação em [VALIDATION.md](VALIDATION.md) e desenho de escala em
[ARCHITECTURE.md](ARCHITECTURE.md). O plano inicial foi atualizado para refletir o código.

## Objetivo e decisões

Transformar posts em eventos, tendências explicáveis e alertas com evidências para
revisão humana. Escolhas: Python/SQLite, feed sintético paginado reproduzível, regras
lexicais por entidade/ação, score determinístico, agente OpenRouter opcional e Streamlit.
A prioridade é demonstrar confiabilidade e raciocínio dentro do escopo reduzido do
exercício. Mastodon é uma fonte pública adicional; X/Instagram não são necessários
quando se usa a simulação permitida no enunciado.

## Cinco temas, eventos que atualizam

| ID | Categoria | Recorte |
|---|---|---|
| `crypto_web3` | Cripto e Web3 | Bitcoin, tokens, protocolos e blockchain |
| `investments_markets` | Investimentos e Mercados | ETFs, juros e mercados |
| `politics_regulation` | Política e Regulação | Consulta/regulação ligada a finanças |
| `business_finance` | Empresas e Finanças | Exchanges, bancos e fintechs |
| `sports_entertainment` | Esportes e Entretenimento | Patrocínio cripto, fan tokens e negócios |

Há principal e no máximo uma secundária; categorias sem suporte ficam `null`.
Categoria não cria cópia do evento. Listagem e incidente da mesma exchange são eventos
distintos. Novas evidências preservam ID e criam snapshot/alerta da versão atual, com
revisão pendente. Classificação humana prevalece na reanálise e muda a versão de
métricas. Rankings por categoria não somam sobreposições em totais globais.

## Contratos essenciais

- Publicação e coleta têm timestamps distintos; engajamento ausente é `null`.
- Página/checkpoint são atômicos, retry é limitado e reexecução é idempotente.
- Cobertura não é inferida da presença de posts. Manifesto local verificado declara
  somente o fixture; APIs públicas continuam parciais.
- Score usa uma janela fechada de quinze minutos e três anteriores cobertas. Sem
  histórico válido, não há score conclusivo. Engajamento é relatado, não pontuado.
- Snapshot congela métricas/evidências por tópico/janela/versão. Alerta e revisão
  histórica não são sobrescritos por uma atualização.
- Agente consulta ambas as ferramentas, com loop limitado e saída validada pelo host.
  Referências inventadas não são substituídas por evidências existentes.
- Observação de um texto não confirma o fato. Confirmação é apenas uma autoridade
  sintética rotulada no cenário; interpretação livre de LLM fica não verificada.
- HHI, origem conhecida e repetição medem o recorte, sem provar manipulação,
  independência de fontes, comunidades ou localização.

## Roteiro de vinte minutos

| Tempo | Demonstração |
|---|---|
| 0–3 min | Arquitetura, fixture sintético, paginação/retry/checkpoint e idempotência |
| 3–7 min | Ranking: Aurora crescendo, Bitcoin estável, Pumplet repetido |
| 7–11 min | Paráfrases, eventos separados, rumor/negação e evento antigo |
| 11–14 min | Ferramentas do agente, referência inventada e instrução maliciosa |
| 14–17 min | APPROVE/EDIT/REJECT, nova versão e revisão anterior preservada |
| 17–20 min | Escala, custos hipotéticos, cobertura e limitações |

A execução real de LLM exige credencial/modelo com tools. Os testes automatizados
usam transporte controlado; mostrar `analysis_mode` evita apresentar simulação como
chamada real. O protótipo não implementa dez milhões de posts/dia; explica essa evolução.

## Limitações e próximos passos

Clustering lexical limitado ao domínio demonstrado; cópias quase iguais ainda não têm
similaridade aproximada; repetição e concentração são sinais, não classificação de bots.
Claims literais não substituem validação semântica/checagem de fatos. Cobertura pública
insuficiente impede baseline conclusivo. SQLite/varreduras de fixtures são locais.
Próximos passos: medir conector/LLM com acesso real, calibrar regras com dataset anotado,
melhorar associação de eventos por tempo, agregar incrementalmente, adicionar backfill,
controle de acesso para hospedagem e medir custo/qualidade. Não há autopublicação.
