---
lang: pt
policy_version: "2"
generated: true
generated_from: "policy/dispute_policy_v1.yaml"
---

# Política de contestação de transações

## O que é esta política {#overview}

Esta política explica como se decide um pedido de contestação de uma transação feita com uma conta ou um cartão. É uma política sintética escrita para este projeto: não é a política de nenhum banco nem de nenhum órgão regulador e não é orientação jurídica. Cada decisão segue regras fixas e fica registrada com um motivo.

## Quais transações podem ser contestadas {#who-can-dispute}

Podem ser contestadas transações destes produtos: Conta poupança, Conta corrente, Cartão de crédito e Cartão de débito.

Os demais produtos (Empréstimo pessoal, Financiamento imobiliário, Investimentos e Seguros) têm canais de atendimento próprios e não são tratados por esta política.

Para apresentar uma contestação, a transação deve ser uma cobrança feita ao cliente (uma compra, um saque, uma transferência ou um pagamento), estar aprovada, não ter data futura, estar dentro do prazo da sua categoria (veja abaixo) e não ter outra contestação em aberto.

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

Depois que a contestação é apresentada, o banco dá a primeira resposta dentro do prazo abaixo, contado em dias corridos a partir da data de apresentação:

- Cobrança não reconhecida: 3 dias.
- Cobrança em duplicidade: 3 dias.
- Valor incorreto: 3 dias.
- Serviço não recebido: 5 dias.
- Contestação por fraude: 1 dia.

## O que ter em mãos {#evidence}

Para cada tipo de contestação, tenha em mãos o seguinte:

- Cobrança não reconhecida: a confirmação de que o cartão continua com você e a indicação de qual parte da cobrança você não reconhece (estabelecimento, data ou valor).
- Cobrança em duplicidade: as datas e os valores das duas cobranças.
- Valor incorreto: um comprovante do valor combinado, como um recibo ou uma confirmação de pedido.
- Serviço não recebido: um comprovante do pedido ou do pagamento e o registro de qualquer tentativa de contato com o estabelecimento.
- Contestação por fraude: a situação do cartão (perdido, roubado ou ainda com você) e a data em que você mesmo usou o cartão pela última vez.

## Confirmação antes de apresentar {#confirmation}

Antes de apresentar uma contestação, o cliente confirma exatamente o que será apresentado: a transação, o motivo e os dados do pedido.

## Quando um atendente analisa o pedido {#human-review}

Mesmo que o pedido cumpra as regras, um atendente o analisa nestes casos:

- É uma contestação por fraude.
- O sistema não conseguiu interpretar o pedido com segurança suficiente.
- Outros critérios de análise do banco se aplicam.

## Contestações por fraude {#fraud-claims}

Toda contestação por fraude é analisada por um atendente. Ela nunca é rejeitada automaticamente, mesmo que a transação tenha sido recusada, esteja fora do prazo ou pertença a um produto que esta política não abrange. Nesses casos, o atendente também recebe o motivo pelo qual o pedido não seria elegível.

## Motivos de cada decisão {#decision-codes}

Cada decisão vem acompanhada de um destes motivos.

| Motivo | Significado |
|---|---|
| `eligible` | A contestação pode ser apresentada depois da sua confirmação. |
| `product_out_of_scope` | O produto não faz parte do escopo desta política. |
| `transaction_type_not_disputable` | O tipo de transação não é uma cobrança que possa ser contestada. |
| `transaction_declined` | A transação foi recusada: não houve cobrança. |
| `transaction_pending` | A transação ainda está pendente. |
| `transaction_reversed` | A transação já foi estornada. |
| `transaction_date_in_future` | A transação tem data futura. |
| `filing_window_expired` | O prazo para apresentar esta contestação expirou. |
| `duplicate_open_case` | Já existe uma contestação aberta para esta transação. |
| `escalate_fraud_claim` | Um atendente analisa este pedido. |
| `escalate_low_nlu_confidence` | Um atendente analisa este pedido. |
| `escalate_repeat_complainer` | Um atendente analisa este pedido. |
| `escalate_amount_above_threshold` | Um atendente analisa este pedido. |
| `escalate_amount_unknown` | Um atendente analisa este pedido. |
| `escalate_risk_score` | Um atendente analisa este pedido. |
