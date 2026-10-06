# Custos do SMILE — roteiro para apresentação

## Mensagem principal

**O custo de IA depende das análises de eventos, das versões e dos tokens, não
automaticamente de cada publicação coletada.** A coleta/normalização, o score e o
agrupamento são determinísticos; a IA recebe candidatos com métricas agregadas e
amostras limitadas de evidências.

Esta estimativa é um exercício de arquitetura para o teste. **Todos os preços são
hipotéticos em USD; não são cotações de fornecedor nem um orçamento de produção.**
O enunciado permite preços hipotéticos quando as premissas ficam explícitas.
Os valores pesquisados pelo candidato podem substituir as tarifas nesta fórmula.

## Quadro para mostrar na demo

| Camada | Premissa | Custo ilustrativo |
|---|---|---:|
| Demo local sem LLM | SQLite/Streamlit locais, fixture sintético, nenhuma chamada externa | US$0 incremental de API/cloud |
| IA em escala — cenário base | 100 análises/dia, 6.000 tokens de entrada e 1.000 de saída por análise, margem de 20% | US$32,40/mês |
| Armazenamento bruto | 10 milhões de posts/dia, 2 KiB/post, retenção de 30 dias, US$0,03/GB-mês | US$18,43/mês em regime de retenção completo |
| Reserva para infraestrutura | Workers, fila, banco de metadados e tráfego; valor arbitrário para o exercício | US$300,00/mês |
| Subtotal das hipóteses acima | IA + armazenamento bruto + reserva | US$350,83/mês |

O subtotal **não comprova que US$350,83 sustentam dez milhões de posts/dia**.
A reserva de infraestrutura precisa ser substituída por dimensionamento e cotações.
Coleta/licenças de fontes podem mudar substancialmente o orçamento; não foram
precificadas. Computador, energia e tempo de desenvolvimento não entram no US$0 da
demo. Não há necessidade de contratar infraestrutura para apresentar este teste.

## Fórmula da IA, com unidades

Defina:

- `A`: análises de candidatos por dia, incluindo novas versões que exigem análise.
- `Tin` e `Tout`: tokens médios por análise, somando todas as rodadas do agente.
- `Pin` e `Pout`: tarifas de entrada/saída em USD por milhão de tokens.
- `m`: margem estimada para retries; `d`: dias do período.

```text
custo_IA_mensal =
  A × d × [(Tin × Pin + Tout × Pout) / 1.000.000] × (1 + m)
```

Premissas: `Tin=6.000`, `Tout=1.000`, `Pin=US$1`, `Pout=US$3`,
`m=20%`, `d=30`.

Uma análise: `(6.000×1 + 1.000×3)/1.000.000 = US$0,009`.
Com margem: `US$0,0108`. Cem análises/dia: `100×30×0,0108 = US$32,40/mês`.

**Uma análise não equivale a uma chamada HTTP.** O agente pode fazer até quatro
chamadas ao modelo. Os 6.000/1.000 são hipóteses de consumo total médio por análise,
não por rodada nem limites máximos. O histórico reenviado em cada request deve ser
contado. A margem de 20% também é uma hipótese, não um dado medido.

Na demo com até cinco candidatos de LLM por execução, as mesmas hipóteses dariam
US$0,054 por execução. O valor real depende dos tokens, modelo e falhas do provedor.

## Sensibilidade: a hipótese que mais importa

Mantendo o volume coletado, retenção, tarifas e reserva de infraestrutura iguais:

| Cenário | Análises de LLM/dia | IA/dia com margem | IA/30 dias | Subtotal com armazenamento e reserva |
|---|---:|---:|---:|---:|
| Base | 100 | US$1,08 | US$32,40 | US$350,83 |
| Mais eventos/versões relevantes | 1.000 | US$10,80 | US$324,00 | US$642,43 |
| Alta frequência de atualização | 10.000 | US$108,00 | US$3.240,00 | US$3.558,43 |

Esses cenários variam somente o componente IA. Manter infraestrutura em US$300 é
uma simplificação de sensibilidade, não garantia de capacidade para os três cenários.
Cem candidatos/dia é uma hipótese de seleção; não foi observado em um feed de dez
milhões de posts. Ela deve ser testada com dados representativos.

Se tokens ou tarifas dobrarem, esse componente de IA dobra. Mais versões de eventos,
evidências longas e falhas podem aumentar a quantidade de análises ou tokens.
Deduplicar publicações não elimina todas as novas análises: uma nova evidência
relevante pode invalidar o cache.

## Armazenamento sem confundir GB e GiB

```text
10.000.000 posts/dia × 2.048 bytes/post = 20,48 GB/dia
20,48 GB/dia × 30 dias = 614,4 GB
614,4 GB × US$0,03/GB-mês = US$18,432/mês ≈ US$18,43
```

Aqui GB é decimal: 1.000.000.000 bytes. O payload é hipoteticamente 2 KiB,
ou 2.048 bytes. A estimativa considera retenção de trinta dias já preenchida;
no primeiro mês, o estoque cresce gradualmente. Índices, réplicas, backups,
anexos/imagens e o custo do banco operacional não estão incluídos nesses 614,4 GB.
Com noventa dias de retenção, seriam 1.843,2 GB e US$55,30/mês apenas de payload,
mantendo as demais premissas.

## O que já limita custo e o que ainda precisa ser medido

| Controle | Estado |
|---|---|
| Coleta/normalização/score sem LLM por post | Implementado |
| Cache por tópico, janela, versão, política e modelo | Implementado |
| Até cinco candidatos com LLM por execução do CLI; demais determinísticos | Implementado; orçamento configurável |
| Até quatro chamadas de modelo, seis tools e oito evidências por consulta | Implementado |
| Texto limitado por evidência, timeout e resposta de rede limitada | Implementado |
| Modo offline e fallback explícito | Implementado; uma tentativa real que falha ainda pode ter custo |
| Cota diária/global de tokens e dinheiro | Próxima etapa; o limite atual é por execução |
| Registrar tokens reais retornados pelo provedor e tarifa/modelo | Próxima etapa |
| Medir CPU, memória, armazenamento, pico, atraso e tráfego | Próxima etapa |
| Precificar acesso/licenciamento de cada fonte | Pendente conforme fonte/contrato |

A quantidade de ferramentas e o cache podem ser inspecionados no protótipo. Ainda
não há consumo real de tokens ou custo de provedor medido nesta entrega.
Modelo gratuito pode tornar uma execução sem cobrança de tokens, mas disponibilidade,
suporte a tools e limites de acesso precisam ser verificados; isso não é premissa
de capacidade ou custo zero permanente em produção.

## Fala de aproximadamente um minuto

> “Eu separei o custo da demonstração do custo de uma operação em escala. A demo roda
> localmente com SQLite e não precisa de API nem cloud. Para dez milhões de posts
> por dia, o desenho usa coleta, deduplicação e score determinísticos; só os candidatos
> selecionados e suas evidências vão para o agente.
>
> Na estimativa, cem análises por dia, com seis mil tokens de entrada e mil de saída
> por análise, custariam trinta e dois dólares e quarenta por mês, incluindo uma
> margem de vinte por cento. Os preços são hipotéticos. Com mil análises por dia,
> esse componente sobe para trezentos e vinte e quatro dólares.
>
> Armazenamento bruto e infraestrutura entram separados. A soma ilustrativa do
> cenário base é cerca de trezentos e cinquenta e um dólares por mês, antes de
> licenças de fontes e outros componentes não dimensionados. Não afirmo que esse
> valor sustenta a escala: preciso medir carga, retenção, tokens e acesso às fontes
> para fechar o orçamento real.”

## Respostas curtas para a banca

**“Por que tão pouco custo de IA para tantos posts?”**
Porque o preço supõe cem análises de eventos/dia, após processamento determinístico,
em vez de uma análise por post. Essa taxa é uma hipótese a validar.

**“Esse total é um compromisso de produção?”**
Não. É uma conta com premissas explícitas. A reserva de infraestrutura não foi
validada por teste de carga, e licenças de fontes ainda não foram cotadas.

**“Como você controla o gasto?”**
Já existem limites por execução, cache e limites do agente. Em produção eu adicionaria
cota global, medição de tokens por análise e alertas de orçamento; margem estimada
não substitui esses controles.

**“E se o tema muda?”**
Uma nova versão relevante pode gerar outra análise e custo. O orçamento deve contar
atualizações, não só novos tópicos.

**“Você mediu o custo real?”**
Nesta rodada, não houve execução contra o LLM real. A demo prova o pipeline e os
limites; os números são uma projeção identificada como hipotética.

Documentos relacionados: [arquitetura](ARCHITECTURE.md), [validação](VALIDATION.md)
e [comandos de execução](README.md).
