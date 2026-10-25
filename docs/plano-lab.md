# Laboratório `testes-consult-lab`: plano de trabalho e de teste

Domingo 25/10, ~02:00. Code freeze 13:00 CET, Live Final 13:30. Pasta própria
(`~/IdeaProjects/Janteiros-2.0-lab`, git worktree), porque outra sessão do Claude trabalhava ao mesmo
tempo na pasta `Janteiros-2.0` (branch `testes-consult`, alterações não commitadas). Nada aqui toca
`main`, `agente-v2`, `experimentos-final` nem `testes-consult`. Nenhuma sessão na arena real foi
aberta pelo laboratório.

**Esta não é a melhor versão possível.** É uma versão mais robusta com o que deu para provar
offline em poucas horas, mais o plano para provar o resto na arena.

## 1. O que o laboratório contém

| Peça | Origem | Estado |
|---|---|---|
| Reader sem autoridade econômica, contexto do histórico entregue, trades a partir de pedidos, writer opcional (`JT_LLM_WRITER=1`) | `testes-consult` (a20e28c) | ligado (writer: texto fixo por padrão) |
| Chaves `JT_X_*`, golden test, eval recusa código sujo, guarda de compromisso no writer, respostas honestas, CSV de forecast, gêmeos, proposta de sondagem, filtro de C do validador, teste de relógio de cauda longa | `agente-v2` (235821c) | ligado |
| **RESCUE**: nunca desistir sem pacote válido; resgate antes da "proposta no teto" | novo | **ligado** |
| **MIRROR**: devolver a alternativa do próprio fornecedor quando ela custa menos (por C) | novo | **ligado** |
| **sim3**: fornecedores fora da distribuição, juiz independente | novo | ferramenta |
| Métricas de cauda no `jt eval` (mediana, p10, pior, taxa de acordo, zeros) | novo | ferramenta |
| Teste com 4 e 5 concorrentes sob latência de cauda longa | novo | teste |
| **GENERIC**: priors só de fatos estruturais | novo | **desligado** (mediu pior) |
| **ROBUST_FC**: ótimo de relacionamento por minimax regret | novo | **desligado** (sem direção clara) |

Com `JT_X_ALL=0` o agente faz exatamente o que o `main` faz (golden test, 15 fornecedores reais).

## 2. O que foi medido (offline, sem LLM, código commitado)

| Config. | arena-sim (3) | sim2 (70): média / p10 | sim3 (105): média / p10 / acordos |
|---|---|---|---|
| A: base do laboratório (merge) | 0.708 | 0.775 / 0.373 | 0.565 / 0.167 / 89.5 % |
| D: + RESCUE (versão 1) | 0.708 | 0.775 / 0.373 | 0.565 / 0.167 |
| B: + GENERIC + ROBUST_FC + RESCUE v1 | 0.411 | 0.763 / 0.316 | 0.561 / 0.141 |
| F: RESCUE antes do teto | 0.708 | 0.775 / 0.373 | 0.565 / 0.167 |
| E: F + ROBUST_FC | 0.708 | 0.770 / 0.299 | 0.567 / 0.167 |
| G: E + GENERIC | 0.536 | 0.763 / 0.316 | 0.561 / 0.141 |
| H: padrão atual (RESCUE + MIRROR), sim3 v2 | 0.708 | 0.775 / 0.373 | 0.566 / 0.157 / 89.5 % |
| I/J: sem MIRROR / sem MIRROR e RESCUE, sim3 v2 | idêntico a H | idêntico | idêntico |

Leitura honesta:
- **O prior genérico perdeu nos três simuladores**, inclusive no sim3 independente. Os priors do `main`
  não são "só overfitting": carregam estrutura útil. Fica disponível como experimento.
- **ROBUST_FC** muda ±0.3 por caso em direções opostas (ganha P03~s2, perde P01n e P03~s1). Sem
  evidência, desligado.
- **RESCUE e MIRROR não mudam nada onde não são necessários** (é o que se espera de regras de
  segurança) e fecham dois buracos reais encontrados pelos testes:
  1. P10 (sem alternativa, must-have de volume): o agente parou por paciência sem ter um pacote
     válido em pé e saiu: **score 0**. Agora manda o pacote válido mais barato que vale para o
     fornecedor o mesmo que a proposta dele.
  2. S03: parou por paciência e a "proposta no teto" saltou de 222 para 248.76, aceita na hora
     (0.21). Agora o resgate vem antes do teto.
- **sim2 e sim3 servem para pegar bugs e medir cauda, não para escolher tática.** Só a arena decide
  tática. O sim3 não usa nenhuma equação do agente nem dados do evento de 10-11/10; seus scores são
  mais baixos que os da arena (fornecedores com até 25 % de margem) e só comparam versões entre si.

Testes: 109 passam (`uv run pytest -q`), incluindo os novos `tests/test_lab.py` e
`tests/test_many_bidders.py`.

Já feito no laboratório (`4a5cbee`): stress 200 variantes limpo (média 0.802, zero problemas) e
**dois ensaios da final seguidos** na arena-sim em tempo real (6/6 acordos cada, zero timeouts,
zero erros, zero bloqueios). Falta só o que depende da arena real (T1-T4) e do release (seção 6).

## 3. Preparar a pasta do laboratório (você, 1 minuto)

```bash
cd ~/IdeaProjects/Janteiros-2.0-lab
cp ../Janteiros-2.0/.env .env          # as chaves (o .env não vai para o git)
uv sync
uv run pytest -q                       # 109 passed
```

## 4. Testes na arena real (só você roda; cada caso = 1 sessão da cota)

A arena é determinística (mesmas propostas → mesmas respostas), então **1 sessão por variante
basta**. Rodar sempre com `JT_NO_LLM=1` ao comparar tática (o texto do LLM muda a resposta do
fornecedor em ~0.3 % do preço; P02 0.854 vs 0.792 com o mesmo código).

Referência a bater = melhor score real de cada caso no `main` / `baseline-r4` (rodada 4):
P01 1.000 · P02 0.792 · P03 0.936 · P04 0.860 · P05 0.834 · P07 0.714 · P08 0.737 · P10 0.686 ·
P11 0.710 · P18 0.677 (média 0.7946).

### T1 · Regressão do laboratório (10 sessões) — o mais importante
```bash
cd ~/IdeaProjects/Janteiros-2.0-lab
JT_NO_LLM=1 uv run jt run --set practice --real --no-llm
uv run jt report
```
- **Passa** se: zero crashes, zero timeouts, 10 acordos, nenhum caso abaixo da referência por mais de
  0.03 (P02 tolerância 0.06).
- O que olhar no relatório: algum `rescue` ou `mirror` disparou? Em qual caso e com que resultado?
  Nos 10 casos de practice nenhum deveria disparar (eles já fechavam bem): se disparar e piorar,
  é bug.

### T2 · Sandbox com dois fornecedores (0 sessões da cota)
```bash
JT_NO_LLM=1 uv run jt run --set sandbox --real --no-llm
```
D01/D02 não gastam cota nem contam no ranking. **Passa** se os dois fecham acordo válido.

### T3 · Só se sobrar tempo e cota: experimentos desligados (6 sessões)
Para decidir se vale ligar na final. Casos de relacionamento (onde ROBUST_FC age) e de matéria-prima
e concorrência (onde GENERIC mais muda):
```bash
JT_NO_LLM=1 JT_X_ROBUST_FC=1 uv run jt run --set practice --real --no-llm --cases case:P01 case:P02 case:P03
JT_NO_LLM=1 JT_X_GENERIC=1  uv run jt run --set practice --real --no-llm --cases case:P05 case:P08 case:P18
```
**Liga na final** só se nenhum dos 3 cair mais de 0.03 e a soma subir. Atenção: os 10 casos de
practice são exatamente onde os priors do `main` foram ajustados, então o `main` tem vantagem aqui
mesmo que generalize pior. Um empate aqui já seria um bom sinal para o GENERIC; uma perda, não.

### T4 · Confirmação com LLM no commit candidato (10 sessões)
```bash
uv run jt run --set practice --real
```
Mesmo critério do T1 (com a tolerância de texto no P02). Se faltar tempo, a final roda sem
`JT_LLM_WRITER` (texto fixo), que é o padrão do laboratório.

**Orçamento total:** 10 (T1) + 0 (T2) + 6 (T3) + 10 (T4) = 26 sessões.

## 5. Critério para o laboratório ir para a final (decisão sua, às ~11:00)

O laboratório substitui o `main` na final **só se**:
1. T1 passa (e T4, se rodado);
2. testes passam, stress limpo (`uv run python scripts/stress.py 200 3`), ensaio da final passa
   duas vezes (`sh scripts/rehearse.sh 8017 r1` e `r2`, na pasta do laboratório);
3. o release de domingo foi carregado e os testes passam com ele (seção 6).

Se algo disso faltar, **a final roda o `main`**, que já tem 0.7946 provado.

## 6. Release de domingo (08:00 CET)

```bash
cd ~/IdeaProjects/Janteiros-2.0
uv run arena data --out data        # baixa o release em data/release-sunday (só dados públicos)
cd ~/IdeaProjects/Janteiros-2.0-lab
ln -sfn ../Janteiros-2.0/data/release-sunday data/release-sunday
uv run pytest -q
```
O `data/package` do laboratório já é um link para o da pasta principal.

## 7. Cronograma sugerido

| Quando (CET) | O que | Quem |
|---|---|---|
| agora | rodar T1 e T2 | você |
| 08:00-08:30 | release de domingo, testes | você (eu confiro) |
| 08:30-10:30 | T3 se T1 passou; correções do que o T1 mostrar | você roda, eu corrijo |
| 10:30-11:00 | T4 (com LLM) no commit candidato | você |
| 11:00 | decisão: laboratório ou `main` | você |
| 11:00-12:30 | stress, 2 ensaios da final, tag | eu preparo, você confere |
| 12:30-13:00 | agente da final rodando (`jt run --set final --real`) | você |

## 8. O que fica para depois (não cabe com segurança antes da final)

- Crença do fornecedor com várias hipóteses e atualização bayesiana (o parecer externo, 4.5).
- Shadow mode de uma política nova ao lado da ativa.
- Memória entre negociações.
- Recalibrar o sim2 só com dados deste evento (hoje ele usa trajetórias de 10-11/10).
- Um prior genérico melhor: ele perdeu porque encolheu a paciência (0.30 contra 0.36-0.40) e a
  âncora em alguns tipos; uma versão que só troque as tabelas por caso pelas mesmas faixas médias
  do `main` é o próximo experimento.

## 9. O que não fazer

- Não rodar a arena real a partir do laboratório com `--real` sem o `.env` copiado e os testes verdes.
- Não ligar GENERIC ou ROBUST_FC na final sem o T3.
- Não usar sim2 ou sim3 como prova de tática.
- Não mexer na pasta `Janteiros-2.0` enquanto a outra sessão estiver ativa nela.

## 10. Resultado do T1 (25/10 ~02:00, commit `10208d8`, sem LLM, rodado a pedido)

Média **0.7895**, 10 acordos, 0 timeouts, 0 erros (`main` melhor: 0.7946).

| Caso | `main` melhor | T1 | Causa (a arena responde igual a propostas iguais) |
|---|---|---|---|
| P02 | 0.792 | **0.875** | mesmas propostas, texto diferente, uma rodada a mais |
| P08 | 0.737 | 0.670 | **= `main`** na rodada de 00:52 com as mesmas propostas; o 0.737 veio de outro texto |
| P18 | 0.677 | 0.610 | **a proposta de sondagem (PROBE)**: 1ª contraproposta 5.30 em vez de 5.20 |
| demais 7 | | iguais | |

Mudança: PROBE desligada por padrão (`f118f3c`); offline igual (arena-sim 0.708, sim2 0.775,
sim3 0.562). Próximo teste: **P18 sozinho** (1 sessão), esperado voltar a ~0.677:
`JT_NO_LLM=1 uv run jt run --set practice --real --no-llm --cases case:P18`.

## 11. Candidato da final (25/10 ~10:10 CET): tag `final-lab` (= `ab6f1bd` + este registro)

- `main` 22f7c26 juntado (release de domingo: "bought forward" = hedged, sem índice de material
  primeiro); golden test regravado (só a ordem de argumentos do P08 mudou).
- 115 testes passam (incl. `tests/test_release.py` com a release no disco); stress 200 limpo (0.802);
  ensaio da final 6/6, 0 timeouts, 0 erros.
- Arena real com a configuração da final (antes do merge, 9c661bb): 0.797, igual em duas rodadas.
- Prior "genérico leve" **não** feito: a hipótese (regras de matéria-prima são de distribuidores)
  estava errada; P18 e P16 são empresas familiares. As regras do `main` usam as mesmas
  características que o gerador da arena usa na final (tipo de caso, de dono, traços).

Comando da final (só você, a partir de ~12:30, depois do freeze):
```bash
cd ~/IdeaProjects/Janteiros-2.0-lab
uv run jt run --set final --real
```
Texto fixo (padrão). Não definir `JT_LLM_WRITER`. Para tirar também o reader do LLM:
`JT_NO_READER=1 uv run jt run --set final --real`.

## 12. Rodada real do `final-lab` (e48acc7, configuração da final, rodada por você ~10:26)

Média 0.7815: 9 casos idênticos à rodada anterior; **P08 0.670 -> 0.515**. Mesmas propostas
(4.82, 4.93); só a ordem dos argumentos mudou pela regra do `main` 22f7c26 (fornecedor hedged:
índice de material por último). Respostas 5.55/5.43/final 5.38 contra 5.52/5.30/final 5.24.
Correção: a reordenação virou a chave `HEDGE_CLAIMS`, desligada por padrão; o reconhecimento de
"bought forward" como hedged continua. Primeira mensagem do P08 de novo idêntica à que fez 0.670
(golden test). Tag `final-lab` movida para este commit.
