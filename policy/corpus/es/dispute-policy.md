---
lang: es
policy_version: "1"
generated: true
generated_from: "policy/dispute_policy_v1.yaml"
---

# Política de disputas de transacciones

## Qué es esta política {#overview}

Esta política explica cómo se decide una solicitud de disputa sobre una transacción hecha con una cuenta o una tarjeta. Es una política sintética escrita para este proyecto: no es la de ningún banco ni regulador, y no constituye asesoría legal. Cada decisión se toma con reglas fijas y queda registrada junto con su motivo.

## Qué transacciones se pueden disputar {#who-can-dispute}

Se pueden disputar transacciones de estos productos: Cuenta de ahorros, Cuenta corriente, Tarjeta de crédito y Tarjeta de débito.

Los demás productos (Préstamo personal, Crédito hipotecario, Inversiones y Seguros) tienen sus propios canales de atención y no se gestionan con esta política.

Para presentar una disputa, la transacción debe ser un cargo al cliente (una compra, un retiro, una transferencia o un pago), estar aprobada, estar dentro del plazo de su categoría (ver más abajo) y no tener otra disputa abierta.

No se pueden disputar depósitos ni ajustes.

Tampoco se pueden disputar transacciones rechazadas, pendientes o revertidas.

## Plazos para presentar una disputa {#filing-windows}

La disputa debe presentarse dentro de un plazo, contado en días calendario desde la fecha de la transacción. El último día del plazo todavía es válido: por ejemplo, con un plazo de 60 días, la disputa se puede presentar el día 60, pero no el día 61.

- Cargo no reconocido: 120 días.
- Cargo duplicado: 60 días.
- Monto incorrecto: 90 días.
- Servicio no recibido: 120 días.
- Reporte de fraude: 180 días.

## Confirmación antes de presentar {#confirmation}

Antes de presentar una disputa, el cliente confirma exactamente lo que se va a presentar: la transacción, el motivo y los datos de la solicitud.

## Cuándo lo revisa un asesor {#human-review}

Aunque la solicitud cumpla las reglas, pasa a revisión de un asesor en estos casos:

- Es un reporte de fraude.
- El sistema no entendió la solicitud con suficiente certeza (confianza inferior al 60 %).
- El cliente ha presentado reclamos repetidos.
- El monto es de 5 000 USD o más.
- No se conoce el monto en dólares.
- El puntaje de riesgo de la transacción es 0,80 o más. El puntaje solo sirve para enviar el caso a revisión; nunca decide el resultado.

## Reportes de fraude {#fraud-claims}

Un asesor revisa siempre los reportes de fraude. Nunca se descartan automáticamente, aunque la transacción haya sido rechazada, esté fuera de plazo o corresponda a un producto fuera del alcance de esta política; en esos casos, el asesor recibe además el motivo por el que la solicitud no habría sido elegible.

## Motivos de cada decisión {#decision-codes}

Cada decisión lleva uno de estos motivos.

| Motivo | Significado |
|---|---|
| `eligible` | La disputa se puede presentar, previa confirmación. |
| `product_out_of_scope` | El producto no está dentro del alcance de esta política. |
| `transaction_type_not_disputable` | El tipo de transacción no es un cargo que se pueda disputar. |
| `transaction_declined` | La transacción fue rechazada: no hubo cargo. |
| `transaction_pending` | La transacción sigue pendiente. |
| `transaction_reversed` | La transacción ya fue revertida. |
| `transaction_date_in_future` | La fecha de la transacción es futura. |
| `filing_window_expired` | Venció el plazo para presentar esta disputa. |
| `duplicate_open_case` | Ya hay una disputa abierta para esta transacción. |
| `escalate_fraud_claim` | Es un reporte de fraude; pasa a revisión de un asesor. |
| `escalate_low_nlu_confidence` | El sistema no entendió la solicitud con suficiente certeza; pasa a revisión de un asesor. |
| `escalate_repeat_complainer` | El cliente tiene reclamos repetidos; pasa a revisión de un asesor. |
| `escalate_amount_above_threshold` | El monto alcanza el umbral de revisión; pasa a revisión de un asesor. |
| `escalate_amount_unknown` | No se conoce el monto en dólares; pasa a revisión de un asesor. |
| `escalate_risk_score` | El puntaje de riesgo alcanza el umbral; pasa a revisión de un asesor. |
