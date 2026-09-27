---
lang: es
policy_version: "2"
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

## Cuándo llega la primera respuesta {#response-time}

Después de presentar una disputa, el banco da una primera respuesta dentro de este plazo, contado en días calendario desde la fecha de presentación:

- Cargo no reconocido: 3 días.
- Cargo duplicado: 3 días.
- Monto incorrecto: 3 días.
- Servicio no recibido: 5 días.
- Reporte de fraude: 1 día.

## Qué tener listo {#evidence}

Para cada tipo de disputa, tenga listo lo siguiente:

- Cargo no reconocido: confirmar que aún tiene la tarjeta y indicar qué parte del cargo no reconoce (comercio, fecha o monto).
- Cargo duplicado: las fechas y los montos de ambos cargos.
- Monto incorrecto: un comprobante del monto acordado, como un recibo o una confirmación de pedido.
- Servicio no recibido: un comprobante del pedido o del pago y cualquier intento de contactar al comercio.
- Reporte de fraude: si la tarjeta está perdida, robada o aún en su poder y cuándo la usó por última vez.

## Confirmación antes de presentar {#confirmation}

Antes de presentar una disputa, el cliente confirma exactamente lo que se va a presentar: la transacción, el motivo y los datos de la solicitud.

## Cuándo lo revisa un asesor {#human-review}

Aunque la solicitud cumpla las reglas, pasa a revisión de un asesor en estos casos:

- Es un reporte de fraude.
- El sistema no entendió la solicitud con suficiente certeza.
- Se aplican otros criterios de revisión del banco.

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
| `escalate_fraud_claim` | Un asesor revisa la solicitud. |
| `escalate_low_nlu_confidence` | Un asesor revisa la solicitud. |
| `escalate_repeat_complainer` | Un asesor revisa la solicitud. |
| `escalate_amount_above_threshold` | Un asesor revisa la solicitud. |
| `escalate_amount_unknown` | Un asesor revisa la solicitud. |
| `escalate_risk_score` | Un asesor revisa la solicitud. |
