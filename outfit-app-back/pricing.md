# Pricing y Coste — Outfit MVP

> Documento dedicado a costes de API. Separado de `contexto_proyecto.md` (arquitectura/decisiones de producto).
> **Actualizar este archivo cuando OpenAI cambie tarifas o cuando midamos coste real tras las primeras generaciones.**

Última verificación de tarifas: **julio 2026** (contra pricing oficial de OpenAI).

---

## 1. Tarifas oficiales verificadas

### Modelos de texto (por 1M tokens)
| Modelo          | Input   | Output  | Rol en la app      |
|-----------------|---------|---------|--------------------|
| `gpt-5.4-nano`  | $0.20   | $1.25   | Texto principal    |
| `gpt-5.4-mini`  | $0.75   | $4.50   | Fallback de texto  |

Desde P10A estas tarifas son invocables desde `estimate_text_cost` (`app/pricing.py`),
que falla ante un modelo sin tarifa verificada igual que `estimate_image_cost`. Las usa
la vía de inspiración, única ruta de texto que persiste su coste, calculado desde el
`usage` devuelto y nunca estimado antes de llamar. La configuración comprueba al
arrancar que los dos modelos de propuestas tienen tarifa.

### Modelo de imagen — `gpt-image-2` (salida por imagen)
| Tamaño | Calidad | Coste de salida | Uso |
|---|---|---:|---|
| 1024×1024 | **low** | **≈ $0.006** | **Elegida para el board del MVP** |
| 1024×1024 | medium | ≈ $0.053 | No usada |
| 1024×1024 | high | ≈ $0.211 | No usada |
| 1024×1536 | **low** | **≈ $0.005** | Salida de la vista puesta opcional implementada |

### Tokens de `gpt-image-2` (por 1M tokens)
| Modalidad | Input | Input cacheado | Output |
|---|---:|---:|---:|
| Texto | $5.00 | $1.25 | — |
| Imagen | $8.00 | $2.00 | $30.00 |

Fuentes oficiales: [guía de generación y cálculo por tamaño](https://developers.openai.com/api/docs/guides/image-generation#cost-and-latency)
y [pricing por modalidad](https://developers.openai.com/api/docs/pricing#image-generation-models).
El coste de salida de `$0.005` **no es el coste completo de una edición**: una llamada
con flat-lay de referencia suma también el texto y los tokens de la imagen de entrada.

---

## 2. Coste por outfit (estimado)

**Supuestos:** 1 llamada de texto + 1 llamada de imagen, ~1.200 tokens de entrada y ~300 de salida en texto, imagen 1024×1024 `low`, 1 imagen, sin voz, sin edición, sin variantes, sin regeneraciones.

### Ruta normal — `gpt-5.4-nano` + imagen `low`
| Componente      | Cálculo                      | Coste       |
|-----------------|------------------------------|-------------|
| Texto (input)   | 1.200 × $0.20 / 1M           | $0.00024    |
| Texto (output)  | 300 × $1.25 / 1M             | $0.000375   |
| Imagen low      | —                            | $0.006      |
| **Total**       |                              | **≈ $0.0066** |

### Ruta fallback — `gpt-5.4-mini` + imagen `low`
| Componente      | Cálculo                      | Coste       |
|-----------------|------------------------------|-------------|
| Texto (input)   | 1.200 × $0.75 / 1M           | $0.0009     |
| Texto (output)  | 300 × $4.50 / 1M             | $0.00135    |
| Imagen low      | —                            | $0.006      |
| **Total**       |                              | **≈ $0.0083** |

### Escalado
| Volumen        | Coste (normal → fallback) |
|----------------|---------------------------|
| 1.000 outfits  | $6.6 – $8.3               |
| 10.000 outfits | $66 – $83                 |

---

## 3. Lectura clave

- **La imagen es ~90% del coste** ($0.006 de $0.0066). El texto es despreciable.
- → **No merece la pena optimizar el proveedor de texto** en el MVP. Ahorrar en nano vs. otro proveedor barato es ahorrar $0.0006/outfit: irrelevante.
- → El único apalancamiento de coste real está en la **imagen**: calidad, nº de imágenes y regeneraciones.

### Qué dispara el coste (por orden de impacto)
1. **Regeneraciones manuales del usuario** — riesgo #1 en una app de moda. Multiplica el coste linealmente. → **Mitigado: límite de 3 (ver §4).**
2. **Subir calidad a medium/high** — ×9 / ×35. → **Mitigado: se arranca en `low` y se valida antes de cambiar.**
3. Generar varias imágenes por outfit → el flat-lay sigue siendo una sola imagen base;
   cada variación y cada vista puesta requieren una acción explícita independiente.
4. Edición posterior de imagen → implementada solo como una vista puesta opcional, una
   por flat-lay, con coste habitual mostrado de ≈ `$0.015` y sin reintentos automáticos.
5. Voz/transcripción → fuera del MVP.
6. Storage sin control → mitigado: disco local en MVP, se revisa al escalar.

---

## 4. Decisiones de pricing tomadas

| # | Decisión | Estado |
|---|----------|--------|
| 1 | Proveedor único: **OpenAI completo** (nano + mini + gpt-image-2). Sin Groq/Gemini/Stability/FAL/etc. en el MVP. | ✅ Decidido |
| 2 | Calidad de imagen: **`low`** a 1024×1024, **1 imagen** por outfit. | ✅ Decidido |
| 3 | Fallback de texto nano→mini decidido por **código** (`needs_fallback()`), no por un estado solicitado por el modelo. | ✅ Implementado; la salida solo admite `ok` o `needs_clarification` |
| 4 | **Límite de 3 regeneraciones manuales** por outfit (1 original + 3 = 4 imágenes máx). | ✅ Implementado (`MAX_REGENERATIONS` en `outfit_service.py`, endpoint `POST /outfits/{id}/regenerate`) |
| 5 | Guardar **coste estimado** y **nº de regeneraciones** por outfit en BD para medir coste real. | ✅ Implementado (tabla `images` con `cost_estimate`; regeneraciones = `COUNT(images) - 1`) |
| 6 | Una sola regeneración activa por outfit, reservada atómicamente en SQLite; dobles pulsaciones reciben `409` antes de llamar a OpenAI. | ✅ Implementado (`regeneration_leases`, con recuperación tras caída) |
| 7 | Vista puesta opcional mediante edición con el flat-lay como referencia; una por imagen, confirmación y coste separados, 0 reintentos automáticos. | ✅ Implementado (`worn_views`; coste desde `usage` o fallback `$0.015`) |

---

## 5. Plan de medición (antes de optimizar nada)

Correr **100–300 generaciones reales** con OpenAI completo y medir:
- % de imágenes buenas a la primera.
- % que necesitan regeneración (→ coste real por outfit = coste base × regeneraciones medias).
- Si `low` es suficiente para **detalles finos** (bordados, texturas, logos mencionados), no solo para distinguir la prenda.
- Si `gpt-5.4-nano` estructura bien el prompt o cuándo hace falta `mini`.
- Coste real por outfit vs. el estimado ($0.0066).

**Regla:** no meter multi-proveedor ni tocar calidad hasta tener estos números.

---

## 6. Resultados del experimento (tanda 1 — 24 descripciones)

Ejecutado con el flujo real (`experiments/run_experiment.py`), set de 24 descripciones variadas: simples, medias, detalle_fino, complejas, one_piece y casos límite.

### Métricas
| Métrica | Resultado |
|---------|-----------|
| Imágenes generadas OK | 21 / 21 intentos con imagen (0 fallos técnicos) |
| `needs_clarification` | 3 / 3 casos límite (heurística cortó bien) |
| **Fallbacks a `mini`** | **0** — `nano` estructuró bien el 100% de los casos |
| Coste total | $0.126 (~$0.006/imagen, = estimación) |
| Regeneraciones que yo haría (muestra 13) | 0 |

### Conclusiones (decisiones confirmadas)
1. **`low` VALIDADO — no se sube a `medium`.** Respeta detalle fino real: bordado floral en mangas, lunares pequeños, cremalleras plateadas, rayas, estampados gráficos. Ahorro de 9× confirmado.
2. **`nano` basta.** 0 fallbacks en 21 casos → el fallback a `mini` queda como red de seguridad, uso marginal esperado.
3. **Coste real ≈ coste teórico ($0.0066/outfit)** — con tasa de regeneración baja, no hay multiplicador significativo.
4. **Composición y restricciones:** 100% cumple (sin modelo/cuerpo/rostro, fondo neutro, prendas separadas, calidad catálogo).

### Punto de atención (no bloqueante)
- **Género no controlado:** al no especificarlo la descripción, el modelo elige estética masc./fem. libremente. No es un fallo del MVP; a considerar más adelante si se busca consistencia.

### Decisión: NO escalar a 100-300 por ahora
Resultado suficientemente consistente para validar el MVP. Escalar aportaría poca info nueva ahora. Se retomará el plan de 100-300 (§5) solo si aparecen dudas de tasa de regeneración en uso real.

---

## 7. Resultados del A/B de composición (tanda 2 — builder v2)

Ejecutado el 2026-07-15 con 12 descripciones complejas de 5-8 piezas. Cada caso se
estructuró una sola vez con `gpt-5.4-nano` y esa misma extracción alimentó dos prompts:

- **A — `flat_list_baseline`:** lista plana determinista, con las mismas frases
  visuales, estilo y restricciones, pero sin zonas corporales rígidas.
- **B — `zoned_builder_v2`:** builder de producción con zonas `top`, `middle`,
  `bottom` y columna lateral.

La revisión fue ciega con etiquetas X/Y y una rúbrica fijada antes de revelar el mapa.
La variante A no reproduce la antigua prosa libre del LLM —esa versión no existe en el
historial Git—, sino una línea base controlada que aísla únicamente el efecto de las
zonas.

### Ejecución y coste

| Métrica | Resultado |
|---|---:|
| Extracciones correctas | 12/12, con 5-8 items según lo esperado |
| Imágenes generadas | 24/24 |
| Fallbacks / errores | 0 / 0 |
| Coste estimado de imagen | $0.1440 |
| Coste estimado de texto | ~$0.0072 |
| Coste completo estimado | **~$0.1512** |
| Latencia media de texto | 6.04 s |
| Latencia media de imagen | 19.11 s |

El coste de texto sigue siendo una estimación: el proyecto todavía no persiste
`completion.usage`, por lo que `$0.1512` no debe presentarse como importe exacto de
factura.

### Puntuación ciega

| Criterio | A: lista plana | B: zonas v2 |
|---|---:|---:|
| Piezas exactas | 12/12 | 12/12 |
| Orden corporal | **12/12** | 11/12 |
| Prendas completas, sin solapamiento | **12/12** | 11/12 |
| Proporciones coherentes | 12/12 | 12/12 |
| Accesorios presentes, no dominantes | 12/12 | 12/12 |
| Detalles explícitos respetados | 12/12 | 12/12 |
| Sin elementos prohibidos | 11/12 | 11/12 |
| **Total** | **83/84 (98.8%)** | **81/84 (96.4%)** |

Fallos concretos:

- Ambas variantes: C03 generó parches con apariencia de logo en las zapatillas altas.
- Solo B: C04 colocó las medias por encima de la falda; el modelo de texto las había
  clasificado como `accessory` y la zona lateral hizo visible ese error semántico.
- Solo B: C10 superpuso kimono y top pese a la restricción de no solapamiento.
- Observación cualitativa: B dejó bastante más espacio vacío y redujo el tamaño de las
  prendas en varios casos complejos.

### Decisión

No subir a formato vertical ni a calidad `medium`: el problema no es de resolución y
ambas opciones aumentarían el coste. El builder zonado supera el umbral prefijado del
90% en orden corporal, pero **no mejora la línea base controlada**.

Esta tanda dejó como acción sustituir las zonas rígidas por un builder determinista y
compacto, conservando el orden por categorías, el cap de accesorios y todas las
restricciones. La implementación y su regresión focalizada se documentan en §8; no fue
necesario repetir las 24 imágenes.

---

## 8. Regresión focalizada del builder adaptativo (tanda 3)

Ejecutada el 2026-07-15 sobre C08 y C10, reutilizando exactamente las extracciones de
la tanda 2. Por tanto, esta prueba no volvió a llamar al modelo de texto y aisló el
cambio de composición.

El builder final mantiene una silueta vertical sencilla con 1-3 piezas. Desde 4 piezas
usa una composición ancha: zona principal de aproximadamente 76%, capas superiores
separadas de exterior a interior, eje inferior y calzado, y rail semántico de accesorios
de aproximadamente 24%. Las medias se tratan como legwear aunque la extracción las
clasifique como accesorio.

> **Nota del 2026-09-01:** el reparto fijo 76/24 descrito en esta sección se sustituyó
> después por una zona principal y un rail proporcionales al número de accesorios
> (16%, 20% o 24%). Con 4 piezas y un solo accesorio, la columna casi vacía inducía
> complementos inventados; el rango medido aquí, C08 y C10, no cubría ese caso. Las cifras
> de esta tanda corresponden a la geometría fija original y siguen siendo su registro
> exacto: la geometría proporcional no se ha vuelto a medir con imágenes reales.

### Ejecución, coste y revisión

| Métrica | Resultado |
|---|---:|
| Extracciones reutilizadas | 2/2 |
| Imágenes generadas | 2/2 |
| Errores | 0 |
| Coste estimado de imagen | **$0.0120** |
| Coste de texto | **$0.0000** |
| Latencia media de imagen | 19.07 s |
| Puntuación visual | **14/14 criterios** |

`$0.0120` es la estimación según la tarifa configurada (2 × $0.006), no una lectura
del importe facturado por OpenAI.

- **C08, 8 piezas:** conserva las ocho piezas; abrigo, blazer y camisa aparecen grandes,
  separados y ordenados de exterior a interior. Pantalón y mocasines mantienen el eje
  corporal, y bufanda, guantes y bolso forman el rail derecho. Reduce claramente el
  espacio vacío de la antigua B.
- **C10, 6 piezas:** conserva las seis piezas y corrige el fallo anterior: kimono y top
  ya no se solapan. El top, pantalón palazzo y sandalias mantienen la lectura corporal;
  collar y bolso quedan en el rail.

El compromiso es deliberado: las capas superiores se muestran lado a lado en vez de
simular una superposición. Así el usuario sigue entendiendo el outfit, pero ninguna
prenda queda oculta ni se inventa cómo cae sobre un cuerpo que el usuario no ha
descrito.

### Decisión final de composición

Se adopta el builder adaptativo en producción. Se mantienen `1024x1024` y calidad
`low`: las dos pruebas resolvieron densidad y solapamiento sin recurrir a retrato ni a
`medium`, por lo que no hay incremento de coste por outfit. C04 no necesitó otra imagen
de pago: el enrutado de medias a legwear es determinista y queda cubierto por pruebas
unitarias. Suite completa en ese cierre: **109 tests en verde**.

Artefactos locales: `experiments/output/20260715_192645_wide_refinement/`.

---

## 9. A/B de vista vestida con referencia (tanda 4)

El 2026-07-20 se comparó sobre C10 una generación solo desde la extracción guardada
contra una edición que recibió el flat-lay existente como referencia. Ambas usaron
`gpt-image-2`, `1024x1536`, calidad `low`, una sola salida y `max_retries=0`. Se
reutilizaron la extracción y la imagen del experimento anterior: **0 llamadas de texto**.

| Variante | Llamadas | Tokens texto entrada | Tokens imagen entrada | Tokens salida | Coste completo | Latencia |
|---|---:|---:|---:|---:|---:|---:|
| Edición con referencia (X) | 1 | 264 | 1,024 | 158 | **$0.014252** | 24.64 s |
| Generación solo texto (Y) | 1 | 243 | 0 | 158 | **$0.005955** | 18.93 s |
| **Total calculado desde `usage`** | **2** | **507** | **1,024** | **316** | **$0.020207** | — |

Los importes de la tabla se calculan multiplicando los tokens devueltos en `usage` por
las tarifas estándar; no son una lectura directa de la factura. No hubo fallbacks,
reintentos ni errores. El presupuesto aprobado de `$0.11` era un
techo conservador previo a conocer la tokenización de la referencia; el uso real quedó
muy por debajo. La edición costó `$0.008297` más que la generación desde texto y tardó
5.71 s más, pero fue la única que conservó la identidad visual del outfit. Se adopta
como ruta opcional; el coste observado de referencia que mostrará el MVP será
**aproximadamente $0.015 por vista**, nunca como precio exacto garantizado.

Artefactos locales ignorados por Git:
`experiments/output/20260720_190229_ab_worn_view/`.

---

## 10. Smoke de la ruta de producto de vista puesta (tanda 5)

El 2026-07-22 se validó la implementación real, no el runner experimental, mediante
`POST /outfits/6/images/6/worn-view`. Se reutilizó una composición local ya persistida
con camiseta azul marino de rayas, vaqueros azules y zapatillas negras.

| Métrica | Presupuesto aprobado | Resultado |
|---|---:|---:|
| Llamadas de texto | 0 | 0 |
| `images.edit` | máximo 1 | 1 |
| Reintentos automáticos | 0 | 0 |
| Errores / fallbacks | 0 | 0 |
| Coste completo | esperado ≈ `$0.015`, techo `$0.10` | **`$0.014137`** |
| Latencia observada | rango UX 20-40 s | ≈ 30 s |

El coste se calculó desde el `usage` devuelto por `gpt-image-2`; no es una lectura de la
factura. La ruta guardó un PNG `1024x1536 low` y exactamente una fila `worn_views` para
la imagen fuente. La galería devolvió esa vista anidada y la reserva de concurrencia quedó
liberada. Una segunda petición idéntica devolvió `created=false` y la misma fila, sin una
nueva llamada al proveedor ni coste adicional.

La revisión visual conservó las tres piezas, el patrón y color de la camiseta, el lavado
y corte general del vaquero y las zapatillas negras. Mostró el maniquí completo sobre
fondo neutro, sin prendas, accesorios, texto o elementos extra. La variación de caída de
la camiseta fue natural al vestirla y no cambió su identidad. El usuario aceptó el
resultado.

**Decisión:** P0 queda cerrado. La ruta de producto confirma la fidelidad, persistencia,
coste, galería e idempotencia que ya estaban cubiertos con mocks. No se justifica otra
generación real hasta que aparezca un fallo visual distinto en uso.

Los PNG permanecen locales e ignorados por Git en `app/generated/`; no se publican como
parte del repositorio.

---

## 11. Búsqueda de productos: línea base por prenda (tanda 6)

Regla vigente desde el 2026-08-07: una búsqueda individual mantiene una única petición
Responses sin reintentos, pero puede completar la investigación con una o dos acciones
`web_search`. El techo previo mostrado pasa a `$0.03`; después se persiste el número real
de acciones y se calcula el coste desde `usage`. Respuestas reales `completed` habían
devuelto dos acciones para una sola prenda y se descartaban después de incurrir el uso,
por lo que exigir exactamente una no protegía el presupuesto ni al usuario. Las métricas
históricas siguientes conservan sus límites y resultados originales.

El 2026-07-27 se ejecutó la primera búsqueda pagada sobre
`pantalones palazzo beige` del outfit 4. Se reutilizó la extracción persistida:
**0 llamadas de extracción**, **0 imágenes**, una respuesta de `gpt-5.4-nano`, una
acción `web_search`, cuatro consultas internas por tienda, 0 reintentos y 0 errores.

| Métrica | Resultado |
|---|---:|
| Páginas fuente consultadas | 11 |
| Candidatos aceptados por URL/dominio | 5 |
| Coincidencias manuales razonables | 4/5 |
| Falso positivo | 1 pantalón bombacho |
| Miniaturas devueltas | 0/5 |
| Tokens de entrada | 8.700 |
| Tokens de salida | 507 |
| Coste fijo de búsqueda | `$0.010000` |
| Coste de tokens | `$0.002374` |
| **Coste total calculado desde `usage`** | **`$0.012374`** |
| Estimación conservadora del runner | `$0.014774` |
| Presupuesto aprobado | `$0.030000` |
| Latencia | 14,146 s |

La valoración 4/5 mide únicamente parecido semántico de tipo y color. En esta primera
aproximación no se descartan ni penalizan resultados agotados; disponibilidad y precio se
conservan como texto bruto. Dos resultados de Zara indicaban falta de stock, pero siguen
contando como coincidencias para esta línea base. Las URLs quedaron respaldadas por las
fuentes de la respuesta y dentro de la allowlist.

Este resultado valida técnicamente la búsqueda textual, pero no decide todavía la
granularidad final. Servirá como referencia frente al siguiente smoke, que agrupará un
outfit persistido completo en una única respuesta con `max_tool_calls=1`.

Artefactos locales ignorados por Git:
`experiments/output/20260727_180433_product_search_real/`.

### Comparación con el outfit completo en una llamada

En la misma fecha se enviaron juntas las cinco prendas persistidas del outfit 5. El
request exigía una entrada por `item_id`, hasta tres candidatos por prenda y limitaba toda
la respuesta a `max_tool_calls=1`. El modelo produjo las cinco entradas, pero concentró
las fuentes útiles y el único candidato en la primera prenda.

| Métrica | Línea base por prenda | Outfit completo |
|---|---:|---:|
| Prendas solicitadas | 1 | 5 |
| Acciones `web_search` | 1 | 1 |
| Consultas internas | 4 | 4 |
| Prendas consultadas internamente | 1/1 | 4/5 |
| Fuentes | 11 | 8 |
| Prendas con candidatos | 1/1 | **1/5** |
| Candidatos totales | 5 | **1** |
| Coincidencias razonables | 4 | 1 |
| Miniaturas | 0 | 0 |
| Tokens de entrada | 8.700 | 8.892 |
| Tokens de salida | 507 | 418 |
| **Coste calculado desde `usage`** | **`$0.012374`** | **`$0.012301`** |
| Latencia | 14,146 s | 6,667 s |

La llamada agrupada ahorró únicamente `$0.000073` (aproximadamente un 0,6 %) y 7,479 s,
pero dejó sin candidato el pantalón palazzo, la falda, la bufanda y los botines. Las
cuatro consultas internas mencionaron camisa, pantalón, falda y bufanda; no hubo consulta
para los botines. Las ocho fuentes recuperadas pertenecían a Zara y la respuesta final
solo conservó una camisa blanca de lino. No hubo errores, fallbacks ni reintentos.

**Decisión:** se descarta buscar un outfit completo con una sola acción web. El ahorro es
irrelevante frente a la pérdida de cobertura. La futura funcionalidad buscará cada prenda
seleccionada de forma independiente, almacenará el resultado y no repetirá la llamada al
reabrir el outfit. Una acción opcional para todo el outfit será internamente un lote
predecible de búsquedas por prenda, con coste total confirmado antes de ejecutarlo. Por
ahora no se filtra ni puntúa la disponibilidad.

Artefactos locales ignorados por Git:
`experiments/output/20260727_182614_product_search_batch_real/`.

### Primer smoke de la ruta de producto

El 2026-07-27 se lanzó una única búsqueda aprobada para la prenda 0 del outfit 7:
`blusa top halter blanca decorados griegos comprar online España`. La petición llegó al
proveedor, pero la ruta respondió HTTP `500` tras 6,165 s porque la respuesta representó
la colección de fuentes como `sources: null` y el parser esperaba siempre una lista.

Se respetó la parada acordada: 1 intento, 0 reintentos y ninguna ampliación del lote. No
se persistió una búsqueda y la reserva del outfit quedó liberada. Como el error ocurrió
antes de guardar `usage`, el coste real no puede calcularse y no debe registrarse como
`$0`; la cuenta podría haber facturado esa petición.

El parser trata ahora las colecciones nulas como vacías y transforma cualquier estructura
de fuentes inesperada en un error controlado. La regresión queda cubierta con mocks.

Tras una aprobación nueva se repitió exactamente la misma petición una vez. La ruta
respondió `200`, persistió la búsqueda y liberó la reserva:

| Métrica | Resultado |
|---|---:|
| Peticiones Responses | 1 |
| Acciones `web_search` | 1 |
| Reintentos | 0 |
| Candidatos aceptados | 3 |
| Miniaturas | 0/3 |
| Tiendas | Zara |
| Tokens de entrada | 8.688 |
| Tokens de salida | 372 |
| Coste fijo de búsqueda | `$0.010000` |
| Coste de tokens | `$0.002203` |
| **Coste total calculado desde `usage`** | **`$0.012203`** |
| Presupuesto aprobado | `$0.030000` |
| Latencia extremo a extremo | 10,626 s |

Los tres resultados eran tops halter blancos. El primero, `TOP HALTER GASA BORDADOS`,
también recogía el detalle decorativo y era el candidato semánticamente más próximo; los
otros dos conservaban tipo y color, pero no el motivo griego. No se asigna porcentaje de
similitud ni se interpreta disponibilidad. La lectura posterior del outfit recuperó los
tres candidatos desde SQLite y confirmó cero reservas pendientes, sin repetir el `POST`.

### Smoke de precisión tras P7B

El 2026-08-07 se ejecutó una búsqueda individual con el prompt reforzado sobre la prenda
3 del outfit 8: `zapatines tipo bailarina negros muy bajitos punta comprar online España`.
El runner experimental permitió cinco candidatos para inspeccionar su orden; la ruta de
producto mantiene su límite de tres. No hubo extracción, generación de imagen, reintento
ni escritura en la base de datos.

| Métrica | Resultado |
|---|---:|
| Peticiones Responses | 1 |
| Acciones `web_search` | 1 |
| Fuentes recuperadas | 10 |
| Candidatos aceptados | 5 |
| Miniaturas | 0/5 |
| Tokens de entrada | 8.810 |
| Tokens de salida | 669 |
| **Coste total calculado desde `usage`** | **`$0.012598`** |
| Estimación conservadora del runner | `$0.014998` |
| Presupuesto aprobado | `$0.030000` |
| Latencia | 8,019 s |

Los cinco candidatos conservaron el tipo bailarina, el color negro y una suela de entre
0,5 y 1 cm. Dos títulos recogieron explícitamente la punta, pero quedaron en tercera y
cuarta posición. El smoke valida una mejora útil de precisión sin otra llamada ni
comparación visual, y deja documentado que la prioridad de un detalle concreto todavía
no es perfecta. Artefactos locales ignorados por Git:
`experiments/output/20260807_121857_product_search_real/`.

### Smoke de regresión del límite de dos acciones

Tras observar respuestas `completed` rechazadas por contener dos acciones web, el
2026-08-07 se ejecutó un smoke dirigido sobre la prenda 0 del outfit 9:
`esmoquin completo azul marino punta de pinguino comprar online España`. Se reutilizó la
extracción persistida y el runner no escribió en SQLite. La respuesta reprodujo la
secuencia problemática con dos `web_search_call`, terminó correctamente y fue aceptada
sin reintento.

| Métrica | Resultado |
|---|---:|
| Peticiones Responses | 1 |
| Acciones `web_search` | 2 |
| Fuentes recuperadas | 17 |
| Candidatos aceptados | 5 |
| Candidatos rechazados | 0 |
| Miniaturas | 0/5 |
| Tokens de entrada | 12.975 |
| Tokens de salida | 1.055 |
| **Coste total calculado desde `usage`** | **`$0.023914`** |
| Estimación conservadora posterior | `$0.028714` |
| Presupuesto aprobado | `$0.040000` |
| Latencia | 10,681 s |

El primer candidato era un traje completo azul marino razonable, pero los cuatro
restantes eran principalmente americanas o blazers y ninguno demostraba la solapa
`punta de pinguino`. El smoke valida la fiabilidad del protocolo de una o dos acciones,
no una precisión semántica perfecta. Se mantiene el máximo de dos, una sola petición
Responses y 0 reintentos; no se justifica ampliar el límite. Artefactos locales ignorados
por Git: `experiments/output/20260807_154714_product_search_real/`.

### Smoke de miniaturas: el proveedor no devuelve imágenes bajo filtro de dominios

El 2026-08-31 se ejecutó una búsqueda individual sobre la prenda 0 del outfit 1
(`camisa blanca lino comprar online España`) con el único objetivo de inspeccionar la
respuesta cruda y decidir si `thumbnail_url` puede llegar alguna vez a la interfaz. No
hubo extracción, generación de imagen, reintento ni escritura en SQLite.

| Métrica | Resultado |
|---|---:|
| Peticiones Responses | 1 |
| Acciones `web_search` | 1 |
| Fuentes recuperadas | 10 |
| Entradas en `web_search_call.results` | 10 |
| De ellas, de tipo `image_result` | **0** |
| Candidatos aceptados | 5 |
| Miniaturas | 0/5 |
| Tokens de entrada | 8.910 |
| Tokens de salida | 567 |
| **Coste calculado desde `usage`** | **`$0.012491`** |
| Estimación conservadora del runner | `$0.014891` |
| Presupuesto aprobado | `$0.035000` |
| Latencia | 7,968 s |

La petición incluía `search_content_types: ["text", "image"]` e
`image_settings: {"max_results": 5, "caption": true}`, y el `include` traía
`web_search_call.results`. El proveedor aceptó la petición y devolvió diez entradas, las
diez de tipo `text_result` con las claves `snippet`, `title`, `type` y `url`. Ninguna
entrada de imagen, ningún `thumbnail_url` ni `image_url`.

Conclusión: con `filters.allowed_domains` restringido a las diez tiendas del mercado ES,
la búsqueda web no devuelve resultados de imagen. Las miniaturas de producto no son
alcanzables por esta vía y la interfaz debe presentar los candidatos solo con tienda,
título, precio y enlace. Esto confirma los `0/5` registrados en los dos smokes del
2026-08-07, que hasta ahora no distinguían entre «el proveedor no las manda» y «llegan y
se pierden al cruzarlas».

Observación secundaria, sin consecuencia de coste: el bucle de
`_search_sources` lee `source_website_url` en cada entrada de `results`, campo que las
entradas `text_result` no tienen —traen `url`—, de modo que ese bucle no aporta ninguna
fuente. No se pierde ningún candidato porque `action.sources` ya recoge las mismas diez
URLs. No se ha podido verificar qué campo usaría una entrada de imagen, porque no llegó
ninguna. Artefactos locales ignorados por Git:
`experiments/output/20260831_152824_product_search_real/`.

**Retirada de la tubería el 2026-09-01, sin coste.** Sobre esta medición se eliminaron
`search_content_types`, `image_settings`, el `include` de `web_search_call.results`, el
bucle que lo leía, el campo `thumbnail_url` del esquema y del contrato compartido, y la
rama de imagen del frontend. El bucle roto se borró en lugar de repararse: `sources` es
la verja que solo acepta candidatos cuya URL declaró el proveedor, y alimentarla con
`results` ampliaría ese criterio sin necesidad demostrada, cuando `action.sources` ya
traía las mismas URLs. Las filas históricas se releen sin migración porque
`ProductCandidate` ignora las claves desconocidas; se verificó sobre las 6 guardadas en
la base local. El smoke de verificación de la subsección siguiente confirmó el contrato
y dejó abierta una duda de coste.

### Smoke posterior a la retirada: contrato correcto y más acciones web

El 2026-09-01, con aprobación explícita y techo de `$0.056`, se ejecutaron dos búsquedas
por el **endpoint de producción** —no por el runner de experimentos, que mantiene su
propia copia del contrato y no ejerce `app/services/openai_product_search.py`—.

| Caso | Prenda | Acciones | Entrada | Salida | Coste |
|---|---|---:|---:|---:|---:|
| A | outfit 1, prenda 0 (camisa, intento 3) | 2 | 12.559 | 740 | `$0.023437` |
| B | outfit 1, prenda 1 (pantalón, intento 1) | 2 | 13.040 | 947 | `$0.023792` |
| | **Total real** | | | | **`$0.047229`** |

Ambas devolvieron 3 candidatos verificables, con tienda, título, precio y enlace, y sin
ningún `thumbnail_url` en la respuesta ni en las filas persistidas. La retirada de la
tubería queda confirmada de extremo a extremo.

**El número de acciones web subió de 1 a 2, por causa ajena a la retirada.** El caso A
repite la consulta exacta del intento 1 (`camisa blanca lino comprar online España`), que
con el código anterior resolvió en **una** acción y `$0.012309`. Las tres mediciones con
el código anterior (intentos 1 y 2 del 2026-08-31 y el smoke de miniaturas del mismo día)
usaron una sola acción con 8.699–8.910 tokens de entrada; las dos posteriores a la
retirada usaron las dos permitidas con 12.559–13.040. El coste por búsqueda pasa de
`~$0.0123` a `~$0.0236`, aún por debajo del techo de `$0.03` que la interfaz anuncia.

### Prueba controlada: la subida no la causó la retirada

Como todas las mediciones antiguas eran del 2026-08-31 y todas las nuevas del
2026-09-01, la fecha quedaba confundida con el cambio de código. Para separarlo se
restauraron temporalmente `search_content_types`, `image_settings` y el `include` de
`web_search_call.results` —sin commitear— y se repitió **la misma prenda, la misma
consulta y el mismo día**, con aprobación propia y techo de `$0.028`.

| Outfit 1, prenda 1 | Petición | Acciones | Entrada | Salida | Coste |
|---|---|---:|---:|---:|---:|
| Intento 1 | sin las claves | 2 | 13.040 | 947 | `$0.023792` |
| Intento 2 | **claves restauradas** | 2 | 12.788 | 670 | `$0.023395` |

Restaurar la petición antigua **no** devuelve la búsqueda a una sola acción: el coste es
prácticamente idéntico. La subida se atribuye a variación del proveedor entre el
2026-08-31 y el 2026-09-01, no a la retirada de la tubería de miniaturas. Se conserva el
código limpio y no se reintroduce ninguna clave.

`max_tool_calls` sigue en 2 y no se tocó en ningún momento; el máximo no subió, solo se
alcanzó. Coste total de la verificación del 2026-09-01: `$0.070624` en tres búsquedas.

---

### Smoke de marca explícita (P9, 2026-09-03)

Ejecutado desde la propia aplicación, que es la superficie real, y no desde el runner:
una búsqueda sobre `camisa de satén` con `Versace` en el campo de detalle.

| | Aprobado | Real |
|---|---|---|
| Peticiones Responses | 1 | 1 |
| Acciones web | hasta 2 | 2 |
| Reintentos | 0 | 0 |
| Coste | techo $0,03 | **$0,023127** |
| Latencia | — | 8,1 s |

Tokens: 12.947 de entrada y 430 de salida. La consulta compuesta fue
`camisa de satén Versace blanca comprar online España`: la marca se conserva como
requisito y los atributos persistidos (satén, blanca) no se pierden. El único candidato
devuelto procede de `www.versace.com`, el dominio oficial antepuesto por la lógica de
marca.

**Lo que este smoke NO prueba.** Versace fabrica camisas de satén, así que el caso no
puede ejercitar la ruta de ausencia real: sigue sin medirse que una marca que no fabrica
esa prenda devuelva lista vacía en lugar de colar otra marca. Comprobarlo exigiría otra
llamada pagada con una combinación deliberadamente inexistente, y no se ha ejecutado.

Un solo candidato frente a los tres que admite el contrato es el comportamiento esperado:
restringir a dominio oficial más distribuidores multimarca reduce mucho el conjunto.

## 12. Smoke de la vía de inspiración (tanda 7, P10C)

Ejecutado el 2026-09-03 con `--proposals --limit 4`. Cuatro situaciones de registro
distinto, solo texto y **cero imágenes**: la calidad de una propuesta se juzga
leyéndola, así que generar boards habría multiplicado el coste sin aportar a la
decisión.

| | Aprobado | Real |
|---|---|---|
| Llamadas de texto | máximo 8 | **4** (ningún fallback) |
| Imágenes | 0 | 0 |
| Coste | techo $0,0482 | **$0,006574** |
| Tokens de entrada | presupuesto 3.000 | 2.451–2.458 |
| Tokens de salida | presupuesto 1.600 | 826–1.074 |
| Latencia | — | 6,9–11,0 s |

El presupuesto de entrada quedó ajustado: el prompt mide 1.951 tokens estimados y el
esquema de structured outputs aporta los ~500 restantes. `tiktoken` no está instalado
en el entorno, así que el conteo previo fue una aproximación por caracteres; el `usage`
real la confirmó dentro del margen.

**Decisión: `gpt-5.4-nano` es suficiente.** Las cuatro comprobaciones objetivas pasaron
en los cuatro casos: las tres propuestas son generables, tienen siluetas distintas,
todas incluyen calzado y ninguna inventó marca. La situación deliberadamente vaga
propuso en vez de pedir aclaración. La temperatura 0.7 produce registros realmente
distintos y no la misma silueta repetida. No se sube a `gpt-5.4-mini`, que queda solo
como fallback por salida inusable.

**Defecto encontrado y corregido sin coste:** 2 de las 12 propuestas prometían en el
título o el resumen un estampado que las prendas no llevaban. El usuario elige leyendo
el título, pero la imagen se compone desde los `items`, así que la promesa no se podía
cumplir. Se añadió al prompt la regla de que título y resumen solo pueden nombrar
atributos presentes en alguna prenda. La corrección no se ha vuelto a medir contra la
API: revalidarla costaría una llamada (~$0,0017).

## 13. Evidencia, reproducibilidad y límite de la medición

- `experiments/run_experiment.py` conserva los modos de smoke, A/B ciego y regresión
  desde extracciones guardadas.
- `experiments/output/` está ignorado por Git porque contiene PNG y CSV voluminosos. Las
  métricas y decisiones que deben sobrevivir al repositorio están registradas en este
  documento; los artefactos brutos permanecen locales.
- Las suites automatizadas usan mocks y cuestan $0. Los comandos experimentales reales
  deben ejecutarse de forma explícita y muestran el presupuesto estimado antes del lote.
- La aplicación no persiste los recuentos de tokens de texto ni del board; esos costes
  siguen siendo estimaciones. Para una vista puesta completada sí calcula y persiste
  `cost_estimate` desde el `usage` devuelto por `gpt-image-2`; si falta, conserva el
  fallback documentado de `$0.015`. El A/B mantiene además los recuentos crudos en su CSV
  local para poder auditar la decisión experimental.

No se repetirá un benchmark de pago para demostrar reproducibilidad de código: CI cubre
la lógica determinista y las llamadas reales solo se justifican ante una nueva hipótesis
visual, un cambio de modelo o drift observado en usuarios.
