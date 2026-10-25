# Plano de trabalho e de teste: branch `experimentos-final`

Domingo 25/10. Code freeze 13:00, Live Final 13:30. Ponto de partida: commit `57683be` (= `main`),
rodada real completa **0.7946** (P01 1.000 · P02 0.792 · P03 0.936 · P04 0.860 · P05 0.834 ·
P07 0.714 · P08 0.737 · P10 0.686 · P11 0.710 · P18 0.677). Zero timeouts e zero crashes nas 4
rodadas reais (34 casos).

Este plano junta a nossa revisão interna e o parecer do engenheiro externo. Não é a versão ideal do
Janteiros: é o que dá para fazer **com segurança em ~10 horas úteis**, com prova na arena.

## 1. Regras do jogo (valem para tudo abaixo)

1. **`main` é o plano B.** Fica no `57683be` e não muda até a decisão de merge (11:00). Se nada
   for provado no branch, a final roda o `main`.
2. **Uma mudança = um interruptor.** Cada experimento tem uma variável de ambiente (`JT_X_...=0/1`)
   para desligá-lo sem tocar no resto. Desligado, o agente faz exatamente o que o `main` faz.
3. **Tática se mede na arena, não no sim2.** O sim2 e o stress test ficam como rede de segurança
   contra bugs (crash, acordo inválido, acordo acima da alternativa, retrocesso).
4. **Medição limpa.** Commit antes de qualquer medição; nenhuma avaliação com código não
   commitado. Cada rodada real anotada em `results/history.jsonl` com o commit e os interruptores.
5. **Tirar o ruído do texto ao medir tática.** O P02 mostrou que o texto do LLM muda a
   contraproposta (~0.3 % do preço). Para comparar táticas, rodamos com `JT_NO_LLM=1` (texto fixo,
   resultado reproduzível). O LLM volta ligado na confirmação final, como decidimos.
6. **Cota:** 400 sessões/dia. Orçamento deste plano: ~70 sessões (sobra muito).

## 2. Experimentos, em ordem de prioridade

Cada um tem: o problema, a mudança, o interruptor, o teste offline e o teste na arena, e o critério
para entrar no `main`.

### E0 · Base de medição (sem mudar tática) · ~30 min
- **Problema:** não temos uma linha de base sem LLM para os 10 casos, e não há testes que
  detectem mudança acidental nas mensagens.
- **Mudança:**
  - tag `baseline-r4` no `57683be`;
  - "golden test": a 1ª mensagem (propostas, argumentos) de cada um dos 15 fornecedores reais
    gravada em arquivo; um teste falha se ela mudar sem querer (com interruptores desligados);
  - `jt eval` passa a recusar rodar com código não commitado (ou marca em vermelho).
- **Arena:** 1 rodada completa com `JT_NO_LLM=1` no `57683be` = **linha de base sem ruído**
  (10 sessões). Tudo daqui em diante é comparado com ela, caso a caso.

### E1 · Writer só fala do que a proposta contém · ~45 min · `JT_X_WRITER_GUARD`
- **Problema:** o writer escreveu "refletimos o volume na proposta" (falso) e o fornecedor cedeu
  menos (P02 0.854 → 0.792). O filtro não barra compromisso nem afirmações sobre termos.
- **Mudança:**
  - o writer recebe a lista do que **de fato** mudou na nossa proposta (ex.: "preço subiu",
    "prazo de pagamento 90 dias") e a instrução de não falar de nenhum outro termo;
  - o filtro passa a recusar: *accept, agree, commit, confirm (the order), guarantee, exclusive,
    award, binding, reflected/included/incorporated in our proposal*, e qualquer menção a volume,
    contrato, prazo de entrega, pagamento ou cláusula que **não** esteja na lista do que mudou;
  - recusado → texto fixo naquela mensagem (como hoje).
- **Offline:** testes com ~20 frases que devem ser barradas e ~10 que devem passar; taxa de
  recusa medida no `llm_smoke`.
- **Arena:** P02 ×3 com LLM (o caso mais sensível ao texto). **Entra** se nenhuma das 3 ficar
  abaixo de 0.80 e o writer não for recusado em mais de 30 % das mensagens.

### E2 · Reader sem autoridade econômica · ~20 min · `JT_X_READER_ECON=0`
- **Problema:** o texto do fornecedor muda parâmetros do modelo (taxa de dinheiro, valor de prazo
  e volume). Ganho medido: zero. Risco de princípio: real.
- **Mudança:** com o interruptor desligado, o reader só registra o que leu (pedidos, tom). Os
  pedidos podem apenas **sugerir** trades (E4), nunca mudar números. As regras fixas por palavra
  (regex) continuam como hoje.
- **Offline:** golden test do E0 continua passando; sim2 igual.
- **Arena:** entra na rodada de confirmação (E9). **Entra** se não piorar nenhum caso > 0.03.

### E3 · Duas propostas "gêmeas" numa cotação concorrida · ~1 h · `JT_X_TWINS`
- **Problema:** foi anunciado para a final. Hoje o agente pode descartar um dos gêmeos cedo
  (por ruído no piso estimado) e perder a concorrência.
- **Mudança:**
  - não descartar um concorrente enquanto o piso estimado dele estiver a menos de ~2 % da melhor
    oferta, nem antes de ele responder duas vezes;
  - com 2 gêmeos, citar a oferta de um para o outro **toda rodada** (já existe) e alternar quem
    recebe o passo maior;
  - o resto igual.
- **Offline:** caso sintético no sim2 com 2 fornecedores com a mesma cotação (e variantes ±1 %),
  verificar que nenhum é descartado antes da 2ª resposta e que a oferta do outro é citada.
- **Arena:** **D02 no sandbox** (2 fornecedores, não gasta cota, não conta leaderboard) ×2 e P05
  ×1. **Entra** se D02 não piorar e P05 continuar ≥ 0.83.

### E4 · Trades a partir do que o fornecedor pede + prova de forecast · ~2 h · `JT_X_TRADES`
- **Problema:** os fornecedores pedem coisas concretas e o agente quase não troca:
  - P04 e P02 pedem "dados verificáveis do volume" (P04 terminou final **porque** não mandamos);
  - P04 pede pagamento mais curto em troca de preço;
  - P11 pede pagamento mais rápido (nesse caso o pagamento não é negociável → nada a fazer);
  - P10 (fábrica cheia): o melhor acordo usa **42 dias** de prazo de entrega; fechamos em 56 e
    pagamos 33 600 EUR de atraso.
- **Mudança:**
  - **prova de forecast:** quando pedirem dados do volume, anexar um CSV gerado só dos dados
    (`forecasts`/`forecast_lines` da peça) e uma frase fixa; uma vez por caso. A arena lê csv;
  - **trade de um campo:** para cada pedido detectado, montar **uma** proposta extra mudando só o
    campo pedido (pagamento, prazo de entrega, contrato), com o preço ajustado para o mesmo valor
    para o fornecedor; só sai se custar menos para nós que a principal e passar em todos os
    portões.
- **Offline:** testes unitários por campo (pedido → proposta válida, mais barata, sem quebrar
  must-have/alternativa; nada sai quando não há ganho); stress test limpo.
- **Arena (template):** P04, P10, P02, P07 ×1. **Entra** se a soma dos 4 subir e nenhum cair
  > 0.03 em relação à linha de base do E0.

### E5 · Custo do fornecedor como intervalo no fechamento · ~1 h · `JT_X_ROBUST_FC`
- **Problema:** um único número (0.92 × cotação) decide a multa de relacionamento; o real variou
  de 0.91 a 0.995.
- **Mudança:** para metas *stabilise*/*secure_supply*, avaliar cada proposta e o fechamento em 3
  cenários de custo (baixo / central / alto). Proposta: a de menor custo no cenário central entre
  as que não pioram mais que X no cenário alto. Fechamento: só se vencer a alternativa nos 3.
- **Offline:** stress test com os 3 seeds (zero acordos acima da alternativa); testes unitários com
  should-cost acima da cotação, ausente, e custo verdadeiro em cada ponta.
- **Arena (template):** P01, P02, P03 ×1. **Entra** se nenhum cair > 0.03.

### E6 · Testes unitários das regras principais · ~45 min (sem interruptor)
Âncora (nunca acima da 1ª contraproposta esperada), segurar só para fundador, tamanho do passo,
fechamento (gap/1.8, gap−cap), nunca retroceder, descarte de concorrente, portão pessimista.
Só testes: protegem as regras enquanto mexemos nas outras coisas.

### E7 · Relógios com cauda longa · ~30 min (só teste)
Em tempo simulado: latências sorteadas (p50 19 s, p90 28 s, p99 55 s), alguns 429 e um 409 no
fechamento; 2, 3 e 4 fornecedores; writer lento. Critério: zero timeouts que levem a perder o caso.

### E8 · Fornecedores "estranhos" para robustez (opcional, se sobrar tempo) · ~1,5 h
Um simulador pequeno com regras **diferentes** das nossas: concede por calendário (ignora o nosso
movimento), pune âncora funda, pune segurar, distribuidor que aceita preço fixo, responde só a
pacotes, ruído alto. Não serve para escolher tática; serve para ver se algum desses comportamentos
faz o agente **quebrar** (crash, acordo ruim, walk sem alternativa, timeout).

### Fica para depois da final (não cabe com segurança)
EvidenceState com proveniência, crença do fornecedor com várias hipóteses, shadow mode, memória
entre negociações, pesquisa pré-negociação. São boas ideias do parecer, mas mexem no núcleo.

## 3. Cronograma (horário de Munique)

| Quando | O que | Arena |
|---|---|---|
| hoje até ~02:00 | E0, E1, E2, E6 (código + testes) | E0: 1 rodada completa sem LLM (10) |
| 08:00 | ensaio do release de teste (`uv run arena data`), conferir leitura | — |
| 08:00–09:00 | E3 (gêmeos), E4 (trades + forecast) | D02 sandbox ×2, P05 ×1 |
| 09:00 | **release real**: `rm -rf data/release-sunday` + `uv run arena data`, conferir, testes | — |
| 09:15–10:30 | E5, E7; medir E4/E5 | P04 P10 P02 P07 P01 P03 (~6–10) |
| 10:30–11:00 | rodada de confirmação **com LLM** no commit candidato, todos os interruptores aprovados ligados | 10 (×2 se der) |
| **11:00** | **decisão de merge** (abaixo) | — |
| 11:00–12:30 | stress (3 seeds), testes, 2 ensaios da final em tempo real, README, tag | — |
| 12:30–13:00 | agente da final rodando e esperando as ondas | — |

## 4. Critério de merge `experimentos-final` → `main` (às 11:00)

Merge **só** se, no commit candidato:
1. 75+ testes passam, stress limpo em 3 seeds (zero must-have quebrado, zero acordo acima da
   alternativa), 2 ensaios da final passam;
2. a rodada de confirmação com LLM tem média **≥ 0.795** e **nenhum caso cai mais de 0.03** em
   relação ao melhor entre `baseline-r4` e a linha de base do E0, salvo P02 (ruído de texto
   conhecido, tolerância 0.06);
3. zero timeouts, zero crashes;
4. cada interruptor ligado tem uma medição na arena que justifica estar ligado; os que não têm,
   ficam **desligados** no merge.

Se não houver tempo para a rodada de confirmação, **não há merge**: a final roda o `main`.

## 5. O que não fazer

- Não mexer no `main` antes das 11:00.
- Não ajustar regra por um único caso sem uma segunda evidência (outro caso ou o mesmo caso 2×).
- Não usar resultado do sim2 como prova de tática.
- Não começar o agente da final antes do release das 09:00 estar baixado e conferido.
