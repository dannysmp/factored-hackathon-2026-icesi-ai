---
lang: pt
policy_version: "2"
generated: true
generated_from: "policy/dispute_policy_v1.yaml"
---

# Política de contestação de transações

## O que é esta política {#overview}

Esta política explica como se decide um pedido de contestação de uma transação feita com uma conta ou um cartão. É uma política sintética escrita para este projeto: não é a de nenhum banco nem regulador, e não constitui orientação jurídica. Cada decisão é tomada com regras fixas e fica registrada com um motivo.

## Quais transações podem ser contestadas {#who-can-dispute}

Podem ser contestadas transações destes produtos: Conta poupança, Conta corrente, Cartão de crédito e Cartão de débito.

Os demais produtos (Empréstimo pessoal, Financiamento imobiliário, Investimentos e Seguros) têm canais de atendimento próprios e não são tratados por esta política.

Para apresentar uma contestação, a transação deve ser uma cobrança ao cliente (uma compra, um saque, uma transferência ou um pagamento), estar aprovada, estar dentro do prazo da sua categoria (veja abaixo) e não ter outra contestação em aberto.

Não podem ser contestados depósitos nem ajustes.

Também não podem ser contestadas transações recusadas, pendentes ou estornadas.

## Prazos para apresentar uma contestação {#filing-windows}

A contestação deve ser apresentada dentro de um prazo, contado em dias corridos a partir da data da transação. O último dia do prazo ainda é válido: por exemplo, com um prazo de 60 dias, a contestação pode ser apresentada no 60º dia, mas não no 61º.

- Cobrança não reconhecida: 120 dias.
- Cobrança em duplicidade: 60 dias.
- Valor incorreto: 90 dias.
- Serviço não recebido: 120 dias.
- Contestação por fraude: 180 dias.

## Quando chega a primeira resposta {#response-time}

Depois de apresentar uma contestação, o banco dá uma primeira resposta dentro deste prazo, contado em dias corridos a partir da data de apresentação:

- Cobrança não reconhecida: 3 dias.
- Cobrança em duplicidade: 3 dias.
- Valor incorreto: 3 dias.
- Serviço não recebido: 5 dias.
- Contestação por fraude: 1 dia.

## O que ter em mãos {#evidence}

Para cada tipo de contestação, tenha isto pronto:

- Cobrança não reconhecida: confirmar que ainda está com o cartão e dizer qual parte da cobrança não reconhece (comerciante, data ou valor).
- Cobrança em duplicidade: as datas e os valores das duas cobranças.
- Valor incorreto: um comprovante do valor combinado, como um recibo ou uma confirmação de pedido.
- Serviço não recebido: um comprovante do pedido ou do pagamento e qualquer tentativa de contato com o comerciante.
- Contestação por fraude: se o cartão está perdido, roubado ou ainda em seu poder e quando você mesmo o usou pela última vez.

## Confirmação antes de apresentar {#confirmation}

Antes de apresentar uma contestação, o cliente confirma exatamente o que será apresentado: a transação, o motivo e os dados do pedido.

## Quando um atendente analisa {#human-review}

Mesmo que o pedido cumpra as regras, ele é encaminhado para análise de um atendente nestes casos:

- É uma contestação por fraude.
- O sistema não entendeu o pedido com segurança suficiente.
- Aplicam-se outros critérios de revisão do banco.

## Contestações por fraude {#fraud-claims}

Toda contestação por fraude é analisada por um atendente. Ela nunca é descartada automaticamente, mesmo que a transação tenha sido recusada, esteja fora do prazo ou seja de um produto fora do escopo desta política; nesses casos, o atendente recebe também o motivo pelo qual o pedido não seria elegível.

## Motivos de cada decisão {#decision-codes}

Cada decisão traz um destes motivos.

| Motivo | Significado |
|---|---|
| `eligible` | A contestação pode ser apresentada, mediante confirmação. |
| `product_out_of_scope` | O produto não faz parte do escopo desta política. |
| `transaction_type_not_disputable` | O tipo de transação não é uma cobrança que possa ser contestada. |
| `transaction_declined` | A transação foi recusada: não houve cobrança. |
| `transaction_pending` | A transação ainda está pendente. |
| `transaction_reversed` | A transação já foi estornada. |
| `transaction_date_in_future` | A data da transação está no futuro. |
| `filing_window_expired` | O prazo para apresentar esta contestação expirou. |
| `duplicate_open_case` | Já existe uma contestação aberta para esta transação. |
| `escalate_fraud_claim` | O caso é encaminhado para análise de um atendente. |
| `escalate_low_nlu_confidence` | O caso é encaminhado para análise de um atendente. |
| `escalate_repeat_complainer` | O caso é encaminhado para análise de um atendente. |
| `escalate_amount_above_threshold` | O caso é encaminhado para análise de um atendente. |
| `escalate_amount_unknown` | O caso é encaminhado para análise de um atendente. |
| `escalate_risk_score` | O caso é encaminhado para análise de um atendente. |
