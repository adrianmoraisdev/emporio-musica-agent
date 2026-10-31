# Empório da Música — Agente de Atendimento

Protótipo de agente de atendimento por mensagens de texto para a **Empório da Música**,
loja fictícia de instrumentos musicais em Campo Grande/MS (desafio técnico AI Engineer).

A **Lia**, assistente virtual da loja, responde em tom de WhatsApp, consulta o catálogo,
os pedidos e o manual de políticas por *function calling* e recusa com gentileza o que
foge do escopo.

Conversas reais com o agente em [`examples/`](examples/).

---

## Como rodar

Pré-requisitos: Python 3.11+ e uma chave da API da Anthropic.

```bash
git clone https://github.com/adrianmoraisdev/emporio-musica-agent.git
cd emporio-musica-agent
python -m venv .venv
.venv\Scripts\activate            # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
cp .env.example .env              # e preencha ANTHROPIC_API_KEY
```

| Comando | O que faz |
|---|---|
| `emporio-agent` | Abre o chat no terminal (`/nova`, `/debug`, `/sair`) |
| `emporio-agent --debug` | Mostra cada ferramenta chamada e o resultado |
| `emporio-agent --sessao joana` | Retoma (ou cria) a conversa salva com esse id |
| `emporio-agent --diagnostico` | Lista as inconsistências encontradas nos dados |
| `python scripts/gerar_exemplos.py` | Regera as conversas de `examples/` |
| `pytest` · `ruff check src tests` · `mypy` | Testes (sem chamar a API), lint e tipos |

Variáveis de ambiente (ver [`.env.example`](.env.example)):

| Variável | Padrão | Uso |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Credencial da API |
| `EMPORIO_MODELO` | `claude-opus-5-5` | Modelo |
| `EMPORIO_ESFORCO` | `low` | Profundidade de raciocínio (`low`/`medium`/`high`) |
| `EMPORIO_HOJE` | data real | Fixa "hoje" (ver suposições) |

---

## Arquitetura

```
mensagem ─► Agente (loop de tool use) ─► Claude ──┐
               ▲        │ executa                  │ decide qual ferramenta
               │        ▼                          │
               │   Ferramentas ──► Catálogo (CSVs tipados + regras de preço)
               │                ├► Políticas (manual em seções + BM25)
               │                └► Encaminhamento p/ equipe (protocolo)
               └── histórico JSON por sessão
```

```
src/emporio_agent/
  catalogo.py     carga/validação dos CSVs, busca, promoções, diagnóstico
  regras.py       regras numéricas do manual (PIX, parcelas, prazos, horário)
  politicas.py    divisão do manual em seções + busca BM25
  ferramentas.py  schemas e handlers das 7 ferramentas expostas ao modelo
  prompt.py       persona e regras do agente (prompt estático)
  agente.py       loop de function calling com a API da Anthropic
  historico.py    persistência de conversas
  cli.py          interface de terminal
data/             dados originais + transcrição do manual (politicas_da_loja.md)
examples/         5 conversas geradas com o agente real
tests/            46 testes (dados, regras, recuperação, ferramentas, loop com cliente falso)
```

### Ferramentas

| Ferramenta | Quando o modelo usa |
|---|---|
| `buscar_produtos` | "violões até R$ 1000", "ukulele elétrico" (filtros de categoria/preço/estoque) |
| `detalhes_produto` | preço, promoção, PIX, parcelas, estoque e alternativas de um item |
| `promocoes_ativas` | "o que está em promoção?" |
| `consultar_pedido` | status, rastreio e prazos de troca, **com verificação do titular** |
| `consultar_politicas` | endereço, horário, pagamento, trocas, frete, garantia, escopo |
| `status_loja` | "estão abertos agora?" e a data de hoje para prazos |
| `encaminhar_para_equipe` | reclamação, devolução a efetivar, pedido de humano |

---

## Decisões técnicas

**Abordagem: *function calling* com ferramentas determinísticas (híbrido dados + RAG).**
O enunciado pede que o agente saiba *quando* consultar dados e *quando* consultar
políticas. Em vez de um roteador fixo ou de um agente de SQL, o modelo escolhe entre
ferramentas bem descritas, e cada uma devolve JSON calculado em Python. Assim:
- **o LLM nunca faz conta**: preço com promoção, PIX não cumulativo, número máximo de
  parcelas e prazos de arrependimento/defeito/garantia saem de `regras.py`, com testes;
- **não há SQL gerado pelo modelo**: nada de consultas erradas, vazamento de dados de
  outros clientes ou injeção; a superfície exposta é só o que cada ferramenta permite;
- o manual da loja exige "nunca informar preço, estoque ou prazos sem consultar o
  sistema", e o design força isso.

**Recuperação das políticas: transcrição em Markdown + BM25 por seção, sem embeddings.**
- A extração do PDF com `pypdf` devolve uma palavra por linha e achata as tabelas.
  Transcrevi o manual para [`data/politicas_da_loja.md`](data/politicas_da_loja.md), e um
  teste confere cada título de seção contra o texto do PDF original para evitar
  divergências.
- O manual tem cerca de 3 mil tokens e 10 seções bem delimitadas. A unidade de
  recuperação é a própria seção; subseções carregam o título da seção-pai para não
  perderem contexto.
- O BM25 em memória, com um pequeno dicionário de sinônimos ("devolver" → arrependimento,
  "parcelar" → parcelamento), recupera a seção certa no top-3 em todos os casos de teste,
  é determinístico e não depende de banco vetorial nem de chamadas extras.
- O sumário das seções vai no prompt, então o modelo também pode pedir uma seção pelo
  número.
- Alternativa considerada: o manual inteiro no prompt, que é viável por ser curto e
  cacheável. Preferi a ferramenta porque deixa explícita a decisão de "consultar
  políticas" (requisito do desafio), escala para manuais maiores e deixa rastro auditável
  no modo debug.

**Modelo e provedor: Claude Opus 5.5 via API da Anthropic, esforço `low`.**
- É forte em uso de ferramentas e em seguir persona e regras.
- Esforço `low` mantém a latência de chat; dá para subir via `.env` em casos complexos.
- O fallback server-side (`fallbacks: "default"`) evita que um falso positivo dos
  classificadores de segurança vire "atendimento fora do ar".
- O modelo é configurável por variável de ambiente.

**Loop manual em vez do *tool runner* do SDK.** O loop tem cerca de 40 linhas e devolve a
lista de ferramentas chamadas a cada turno, o que alimenta o `--debug` e as transcrições
de `examples/`. Ele também desfaz o turno se a API falhar, para o histórico nunca ficar
inválido.

**Estratégia de prompt.**
- O prompt de sistema é estático, o que permite o cache de prompt (`cache_control`).
- Ele é organizado em blocos XML: `tom`, `fontes_de_verdade`, `regras_de_resposta`,
  `escopo` e `manual_sumario`.
- Data/hora e dados do cliente nunca entram no prompt: chegam pelas ferramentas.
- As descrições das ferramentas dizem *quando* usar cada uma, em vez de só *o que* fazem.

**Privacidade (LGPD) no desenho.**
- `consultar_pedido` exige o número do pedido mais telefone, e-mail ou nome completo do
  titular.
- A resposta de erro é a mesma para "pedido não existe" e "dados não conferem", então não
  revela a existência do pedido.
- Há limite de 3 tentativas por pedido na sessão.
- O resultado não devolve telefone nem e-mail.

**Persistência do histórico.**
- Uma conversa de atendimento é retomada com frequência ("e aquele violão de ontem?").
- Cada sessão vira um JSON em `.cache/conversas/`, append-only e com escrita atômica,
  guardando os blocos exatamente como a API devolveu.
- Em produção seria Redis ou Postgres indexado pelo telefone do WhatsApp.

**Interface: CLI** com `rich`. O foco do desafio é o agente; a classe `Agente` não
conhece a CLI e pode ser exposta numa API (FastAPI/webhook do WhatsApp) sem mudanças.

---

## Tratamento dos dados e suposições

O comando `emporio-agent --diagnostico` lista os problemas abaixo, que foram
encontrados nos dados e são tratados no código:

| Achado | Decisão |
|---|---|
| Produto 96 com status `active` e estoque 0 | Tratado como "temporariamente esgotado" e o agente oferece alternativas |
| Produtos `discontinued` (113, 136) e `coming_soon` (130) | Fora das buscas por padrão; mensagem específica para cada status |
| Promoções: 25 cadastradas, só 4 ativas | Só `is_active = 1` vale; se houver duas ativas no mesmo item, vale a maior (não cumulativas) |
| Pedidos 3 e 20: total ≠ soma do preço de tabela | O total do pedido é o valor histórico pago; nunca é recalculado |
| Pedidos 3 e 14 contêm o produto 130 (`coming_soon`) | Inconsistência de origem, apenas reportada |
| Categorias 6, 7 e 8 (sopros, cordas orquestrais) sem produtos | O agente responde que não há itens disponíveis em vez de inventar |
| Clientes 11 e 43 com o mesmo e-mail | A verificação é sempre do titular *daquele* pedido, então não há vazamento |
| Não existe data real de recebimento | Prazos de arrependimento e garantia contam a partir da `estimated_delivery` (sinalizado ao modelo) |
| Pedidos de out/2025 a mar/2026 | `EMPORIO_HOJE=2026-03-25` no `.env.example` deixa status e prazos coerentes; sem ele, vale a data real |

Ambiguidades do manual, com a interpretação adotada:
- **Parcelamento:** a tabela da §3 diz "parcela mínima de R$ 100" para crédito, enquanto
  a §3.1 detalha faixas de R$ 50, R$ 80 e R$ 100. Prevalece a §3.1, que é mais específica.
  "Até 3x sem valor mínimo (exceto abaixo de R$ 50)" foi lido como parcela mínima de R$ 50.
- **PIX:** os 5% são sobre o preço de tabela e não se aplicam a itens em promoção (§6.2).
- **Telefones:** a §1.2 traz (67) 3341-4444 como telefone/WhatsApp e a §7 traz
  (67) 3321-4500 como WhatsApp. Ambos ficam no manual e o agente cita conforme a seção
  recuperada.
- **E-mail:** a §1.2 traz o e-mail com acento (`empório`), que é inválido. Foi usado o da
  capa, sem acento (única correção na transcrição).
- **Horário:** o manual não tem calendário de feriados, então `status_loja` considera só
  os dias da semana.

---

## Limitações conhecidas e próximos passos

- **Avaliação automatizada:** os testes cobrem dados, regras e recuperação de forma
  determinística, mas a qualidade das respostas do LLM foi verificada à mão. Próximo
  passo: um conjunto de avaliação com perguntas e respostas esperadas, e um LLM-juiz para
  tom e aderência às políticas, rodando a cada mudança de prompt.
- **Busca de produtos lexical:** pede sinônimos manuais e não entende "algo para tocar
  rock pesado". Com um catálogo maior, eu usaria embeddings e busca híbrida.
- **Sem canal real:** o próximo passo é um webhook do WhatsApp com FastAPI, histórico em
  Redis e mensagens fora do expediente (o manual pede que o assistente informe o horário
  de retorno).
- **Encaminhamento para humano:** hoje grava em JSONL. Em produção iria para o
  helpdesk/CRM, com notificação à equipe.
- **Frete:** o manual diz que o valor depende do CEP, peso e dimensões, que não existem nos
  dados. O agente informa prazos e regras, não valores.
- **Streaming:** as respostas chegam de uma vez. Para chat em tempo real eu usaria
  `messages.stream`.
- **Observabilidade:** além do `--debug`, faltam logs estruturados de tokens, latência e
  ferramentas por conversa (OpenTelemetry).

---

## Uso de assistentes de código

Usei o **Claude Code** (modelo Claude Opus 5.5) como par de programação durante todo o
desafio. Workflow:

1. **Análise:** o assistente leu o enunciado, o PDF de políticas e os CSVs, e fez uma
   auditoria cruzada dos dados (totais de pedido × itens, status, promoções ativas). Daí
   saiu a tabela de suposições acima.
2. **Decisões:** discutimos as alternativas (manual inteiro no prompt × RAG, tool runner
   × loop manual, provedor). Eu escolhi o provedor e as convenções do repositório; as
   justificativas estão neste README.
3. **Implementação incremental:** cada camada (dados → políticas → ferramentas → agente)
   foi feita com testes antes do commit, com `ruff` e `mypy --strict` passando. O
   histórico de commits reflete essa ordem.
4. **Revisão:** revisei os diffs e as regras de negócio contra o manual, sobretudo os
   cálculos de parcelamento e prazos, e validei as conversas de `examples/` geradas com o
   agente real.

A IA acelerou a parte braçal (transcrição do manual, testes parametrizados, boilerplate de
CLI). As decisões de produto, a interpretação das ambiguidades e a verificação final
ficaram comigo.
