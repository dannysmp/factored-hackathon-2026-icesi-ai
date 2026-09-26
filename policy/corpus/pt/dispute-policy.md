---
lang: pt
policy_version: "1"
generated: true
---

# Política de contestação de transações

Gerado a partir da política `policy/dispute_policy_v1.yaml` (versão 1). Não editar à mão: qualquer mudança é feita na política e regenerada.

## O que é esta política {#overview}

Esta política explica como se decide um pedido de contestação de uma transação de contas e cartões. É uma política sintética escrita para este projeto: não é a de nenhum banco nem regulador, e não é aconselhamento jurídico. Cada decisão é tomada com regras fixas e fica registrada com um motivo.

## Quais transações podem ser contestadas {#who-can-dispute}

Podem ser contestadas as transações destes produtos: Cuenta Ahorro, Cuenta Corriente, Tarjeta Crédito e Tarjeta Débito.

Os demais produtos (Préstamo Personal, Préstamo Hipotecario, Inversión e Seguro) têm seus próprios processos de reclamação e não são contestados aqui.

Para apresentar uma contestação, a transação deve ser uma cobrança ao cliente (pagamento, compra, transferência e saque), ter status aprovado, estar dentro do prazo da sua categoria (veja abaixo) e não ter outra contestação aberta.

Não podem ser contestadas as transações destes tipos: depósito e ajuste.

Não podem ser contestadas as transações com status recusado, pendente ou estornado.

## Prazos para apresentar uma contestação {#filing-windows}

A contestação deve ser apresentada dentro de um prazo, contado em dias a partir da data da transação. O último dia válido é o dia indicado pelo prazo; no dia seguinte já não pode ser apresentada.

- Cobrança não reconhecida: 120 dias.
- Cobrança duplicada: 60 dias.
- Valor incorreto: 90 dias.
- Serviço não recebido: 120 dias.
- Alegação de fraude: 180 dias.

## Confirmação antes de apresentar {#confirmation}

Quando uma contestação pode ser apresentada, e antes de apresentá-la, o cliente confirma a apresentação exata (transação, motivo e dados).

## Quando uma pessoa revisa {#human-review}

Mesmo que o pedido cumpra as regras, uma pessoa o revisa nestes casos:

- É uma alegação de fraude.
- O pedido não foi entendido com segurança suficiente (menos de 60%).
- O cliente apresentou reclamações repetidas.
- O valor é de 5.000 USD ou mais.
- O valor em dólares não é conhecido.
- A pontuação de risco da transação é 0,80 ou mais. A pontuação só decide que uma pessoa revise; nunca decide o resultado.

## Alegações de fraude {#fraud-claims}

Uma alegação de fraude é sempre revisada por uma pessoa. Nunca é recusada automaticamente, mesmo que a transação tenha sido recusada, esteja fora do prazo ou seja de um produto fora do alcance: nesse caso a pessoa recebe o motivo pelo qual a regra teria falhado.

## Motivos de cada decisão {#decision-codes}

Cada decisão traz um destes motivos.

| Motivo | Significado |
|---|---|
| `eligible` | A contestação pode ser apresentada, mediante confirmação. |
| `product_out_of_scope` | O produto não está no alcance desta política. |
| `transaction_type_not_disputable` | O tipo de transação não é uma cobrança que possa ser contestada. |
| `transaction_declined` | A transação foi recusada: não houve cobrança. |
| `transaction_pending` | A transação ainda está pendente. |
| `transaction_reversed` | A transação já foi estornada. |
| `transaction_date_in_future` | A data da transação é posterior a hoje. |
| `filing_window_expired` | O prazo para apresentar esta contestação venceu. |
| `duplicate_open_case` | Já existe uma contestação aberta para esta transação. |
| `escalate_fraud_claim` | É uma alegação de fraude; passa para revisão de uma pessoa. |
| `escalate_low_nlu_confidence` | O pedido não foi entendido com segurança suficiente; passa para revisão de uma pessoa. |
| `escalate_repeat_complainer` | O cliente tem reclamações repetidas; passa para revisão de uma pessoa. |
| `escalate_amount_above_threshold` | O valor atinge o limite de revisão; passa para revisão de uma pessoa. |
| `escalate_amount_unknown` | O valor em dólares não é conhecido; passa para revisão de uma pessoa. |
| `escalate_risk_score` | A pontuação de risco atinge o limite; passa para revisão de uma pessoa. |
