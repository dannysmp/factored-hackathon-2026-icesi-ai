---
lang: es
policy_version: "1"
generated: true
---

# Política de disputas de transacciones

Generado a partir de la política `policy/dispute_policy_v1.yaml` (versión 1). No editar a mano: cualquier cambio se hace en la política y se regenera.

## Qué es esta política {#overview}

Esta política explica cómo se decide una solicitud de disputa sobre una transacción de cuentas y tarjetas. Es una política sintética escrita para este proyecto: no es la de ningún banco ni regulador, y no es asesoría legal. Cada decisión se toma con reglas fijas y queda registrada con un motivo.

## Qué transacciones se pueden disputar {#who-can-dispute}

Se pueden disputar las transacciones de estos productos: Cuenta Ahorro, Cuenta Corriente, Tarjeta Crédito y Tarjeta Débito.

Los demás productos (Préstamo Personal, Préstamo Hipotecario, Inversión y Seguro) tienen sus propios procesos de reclamo y no se disputan aquí.

Para presentar una disputa, la transacción debe ser un cargo al cliente (pago, compra, transferencia y retiro), tener estado aprobado, estar dentro del plazo de su categoría (ver más abajo) y no tener ya otra disputa abierta.

No se pueden disputar las transacciones de estos tipos: depósito y ajuste.

No se pueden disputar las transacciones con estado rechazado, pendiente o revertido.

## Plazos para presentar una disputa {#filing-windows}

La disputa debe presentarse dentro de un plazo, contado en días desde la fecha de la transacción. El último día válido es el día que indica el plazo; al día siguiente ya no se puede presentar.

- Cargo no reconocido: 120 días.
- Cargo duplicado: 60 días.
- Monto incorrecto: 90 días.
- Servicio no recibido: 120 días.
- Reclamo de fraude: 180 días.

## Confirmación antes de presentar {#confirmation}

Cuando una disputa se puede presentar, y antes de presentarla, el cliente confirma la presentación exacta (transacción, motivo y datos).

## Cuándo lo revisa una persona {#human-review}

Aunque la solicitud cumpla las reglas, la revisa una persona en estos casos:

- Es un reclamo de fraude.
- No se entendió la solicitud con suficiente seguridad (menos de 60 %).
- El cliente ha presentado reclamos repetidos.
- El monto es de 5.000 USD o más.
- No se conoce el monto en dólares.
- El puntaje de riesgo de la transacción es 0,80 o más. El puntaje solo decide que la revise una persona; nunca decide el resultado.

## Reclamos de fraude {#fraud-claims}

Un reclamo de fraude siempre lo revisa una persona. Nunca se rechaza automáticamente, aunque la transacción esté rechazada, fuera de plazo o de un producto fuera de alcance: en ese caso la persona recibe el motivo por el que la regla habría fallado.

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
| `transaction_date_in_future` | La fecha de la transacción es posterior a hoy. |
| `filing_window_expired` | Venció el plazo para presentar esta disputa. |
| `duplicate_open_case` | Ya hay una disputa abierta para esta transacción. |
| `escalate_fraud_claim` | Es un reclamo de fraude; pasa a revisión de una persona. |
| `escalate_low_nlu_confidence` | No se entendió la solicitud con suficiente seguridad; pasa a revisión de una persona. |
| `escalate_repeat_complainer` | El cliente tiene reclamos repetidos; pasa a revisión de una persona. |
| `escalate_amount_above_threshold` | El monto alcanza el umbral de revisión; pasa a revisión de una persona. |
| `escalate_amount_unknown` | No se conoce el monto en dólares; pasa a revisión de una persona. |
| `escalate_risk_score` | El puntaje de riesgo alcanza el umbral; pasa a revisión de una persona. |
